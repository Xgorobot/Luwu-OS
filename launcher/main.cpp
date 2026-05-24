#include <QApplication>
#include <QStackedWidget>
#include <QLabel>
#include <QTimer>
#include <QFileSystemWatcher>
#include <fcntl.h>
#include <unistd.h>
#include <QDateTime>
#include <QProcess>
#include <QProcessEnvironment>
#include <QDebug>
#include <QElapsedTimer>
#include <QKeyEvent>
#include <QFile>
#include <QPixmap>
#include <QPainter>
#include <QFont>
#include <QCursor>
#include <sys/stat.h>
#include <unistd.h>
#include "keyfilter.h"
#include "galleryview.h"
#include "demogridview.h"
#include "statusbar.h"

// 检测是否有鼠标连接
static bool isMouseConnected() {
    QFile f("/proc/bus/input/devices");
    if (f.open(QIODevice::ReadOnly | QIODevice::Text)) {
        QString content = f.readAll().toLower();
        f.close();
        return content.contains("mouse");
    }
    return false;
}

// 记住用户上次在主菜单选中的卡片索引，下次启动自动恢复
static constexpr const char *LAST_CARD_FILE = "/home/pi/luwu-os/configs/last_card";

static void saveLastCardIndex(int idx) {
    // 只记录图形化编程(1)和AI交互(2)，配网/示例/设置不需要记住
    if (idx != 1 && idx != 2) return;
    QFile f(LAST_CARD_FILE);
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        f.write(QByteArray::number(idx));
        f.close();
    }
}

static int loadLastCardIndex() {
    QFile f(LAST_CARD_FILE);
    if (f.open(QIODevice::ReadOnly)) {
        bool ok = false;
        int idx = f.readAll().trimmed().toInt(&ok);
        f.close();
        if (ok && idx >= 0 && idx < CARD_COUNT) {
            return idx;
        }
    }
    // 没有记录时默认停在无线网络，方便新用户先配网
    return 0;
}

// ========================================================================
// 配置常量
// ========================================================================
static constexpr const char *PRELOAD_SCRIPT = "/home/pi/luwu-os/apps/demo_page/preload_app.py";
static constexpr const char *FIFO_PATH = "/tmp/luwu_preload.fifo";
static constexpr const char *KEYS_FIFO = "/tmp/luwu_keys.fifo";

// ========================================================================
// Main
// ========================================================================
int main(int argc, char *argv[]) {
    QApplication app(argc, argv);

    // 全局默认字体：Noto Sans CJK SC（供真 Medium/Regular 多字重，避免 Qt 伪粗体描边）
    {
        QFont appFont("Noto Sans CJK SC");
        appFont.setStyleStrategy(QFont::PreferAntialias);
        QApplication::setFont(appFont);
    }

    // --- 页面栈 ---
    QStackedWidget stack;
    stack.setWindowTitle("Luwu OS");

    GalleryView *gallery = new GalleryView(&stack);
    DemoGridView *demoGrid = new DemoGridView(&stack);

    // 根据上次记录设置启动时默认选中的卡片，无记录时默认图形化编程(1)
    int startIndex = loadLastCardIndex();
    gallery->setStartIndex(startIndex);
    qDebug() << "[luwu-launcher] last_card startIndex=" << startIndex;

    stack.addWidget(gallery);   // index 0: 主菜单
    stack.addWidget(demoGrid);  // index 1: demo 网格页
    stack.setCurrentIndex(0);

    // --- 预加载子进程管理 ---
    auto *preloadProc = new QProcess(&stack);
    preloadProc->setProcessChannelMode(QProcess::ForwardedChannels);

    QElapsedTimer launchTimer;

    KeyFilter *keyFilter = nullptr;  // early decl for lambdas below
    int preAppPage = 0;               // 启动 app 前记录当前页面索引

    auto startPreload = [&]() {
        unlink(FIFO_PATH);
        if (mkfifo(FIFO_PATH, 0666) != 0) {
            qWarning() << "[luwu-launcher] mkfifo failed";
        }

        QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
        env.insert("QT_QPA_PLATFORM", "linuxfb:fb=/dev/fb-spi");
        env.insert("QT_QPA_FONTDIR", "/usr/share/fonts");
        env.insert("PYTHONUNBUFFERED", "1");
        preloadProc->setProcessEnvironment(env);
        preloadProc->setProgram("python3");
        preloadProc->setArguments({PRELOAD_SCRIPT});
        preloadProc->start();

        qint64 t = QDateTime::currentMSecsSinceEpoch();
        qDebug().noquote() << QString("[luwu-launcher][%1] preload started").arg(t);
    };

    QObject::connect(preloadProc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
        [&](int code, QProcess::ExitStatus st) {
            qint64 total = launchTimer.elapsed();
            qDebug().noquote() << QString("[luwu-launcher][%1] PySide finished code=%2 status=%3 total=%4ms")
                                      .arg(QDateTime::currentMSecsSinceEpoch()).arg(code).arg(int(st)).arg(total);
            keyFilter->blocked = false;
            unlink(KEYS_FIFO);
            // 恢复桌面显示：先 show + force paint，再切回进入前的页面
            stack.showFullScreen();
            QApplication::processEvents();
            stack.repaint();
            QApplication::processEvents();
            stack.setCurrentIndex(preAppPage);
            stack.repaint();
            QApplication::processEvents();
            if (preAppPage == 0) {
                gallery->setFocus();
            } else {
                demoGrid->setFocus();
            }
            // 延迟恢复 preload 进程，同时兜底重绘防黑屏
            QTimer::singleShot(300, &stack, [&]() {
                stack.repaint();
                startPreload();
            });
        });

    auto launchApp = [&](const QString &script) {
        if (preloadProc->state() == QProcess::NotRunning) {
            qDebug() << "[luwu-launcher] preload not running, starting now...";
            startPreload();
            return;
        }
        // 记录当前页面，返回时恢复
        preAppPage = stack.currentIndex();
        // Hide launcher so child app gets framebuffer + key events
        unlink(KEYS_FIFO);
        mkfifo(KEYS_FIFO, 0666);
        stack.hide();
        keyFilter->blocked = true;
        qint64 t_req = QDateTime::currentMSecsSinceEpoch();
        qDebug().noquote() << QString("[luwu-launcher][%1] request -> trigger (%2)")
                                  .arg(t_req).arg(script);

        stack.repaint();
        launchTimer.restart();

        QFile fifo(FIFO_PATH);
        if (fifo.open(QIODevice::WriteOnly)) {
            QByteArray line = script.toUtf8() + '\n';
            fifo.write(line);
            fifo.close();
            qint64 t_done = QDateTime::currentMSecsSinceEpoch();
            qDebug().noquote() << QString("[luwu-launcher][%1] FIFO written +%2ms")
                                      .arg(t_done).arg(t_done - t_req);
        } else {
            qWarning() << "[luwu-launcher] failed to open FIFO for writing";
            stack.showFullScreen();
        }
    };

    startPreload();

    // --- 按键处理（keyfilter 全局拦截，按当前页面分发） ---
    keyFilter = new KeyFilter(&stack);
    keyFilter->onKey = [&](QKeyEvent *ke) {
        // When child app is running, forward keys via FIFO
        if (keyFilter->blocked) {
            int kfd = ::open(KEYS_FIFO, O_WRONLY | O_NONBLOCK);
            if (kfd >= 0) {
                QByteArray line = QByteArray::number(ke->key()) + '\n';
                ::write(kfd, line.constData(), line.size());
                ::close(kfd);
            }
            return;
        }
        const char *name = "?";
        int currentPage = stack.currentIndex();

        if (currentPage == 0) {
            // ========== GalleryView (主菜单) ==========
            switch (ke->key()) {
            case Qt::Key_Left:
                name = "KEY_LEFT(A)->LEFT";
                if (!gallery->isAnimating()) { gallery->moveSelection(-1); }
                break;
            case Qt::Key_Right:
                name = "KEY_RIGHT(B)->RIGHT";
                if (!gallery->isAnimating()) { gallery->moveSelection(1); }
                break;
            case Qt::Key_Return: {
                name = "KEY_ENTER(D)->ENTER";
                if (!gallery->isAnimating()) {
                    int idx = gallery->selectedIndex();
                    if (idx == 3) {
                        // Demo 卡片 → 切换到 demo 网格页
                        qDebug() << "[luwu-launcher] opening demo grid...";
                        demoGrid->resize(stack.size());
                        stack.setCurrentIndex(1);
                        demoGrid->setFocus();
                    } else {
                        QString app = gallery->selectedAppPath();
                        if (QFile::exists(app)) {
                            saveLastCardIndex(idx);
                            launchApp(CARDS[idx].appPath);
                        } else {
                            qDebug() << "[luwu-launcher] app not found:" << app;
                        }
                    }
                }
                break;
            }
            case Qt::Key_Back:
                name = "KEY_BACK(C)->BACK";
                break;
            }
        } else {
            // ========== DemoGridView (demo 网格页) ==========
            switch (ke->key()) {
            case Qt::Key_Left:
                name = "KEY_LEFT(A)->LEFT";
                demoGrid->moveSelection(-1);
                break;
            case Qt::Key_Right:
                name = "KEY_RIGHT(B)->RIGHT";
                demoGrid->moveSelection(1);
                break;
            case Qt::Key_Return:
                name = "KEY_ENTER(D)->ENTER";
                {
                    QString demoPath = demoGrid->selectedDemoPath();
                    if (!demoPath.isEmpty()) {
                        launchApp(demoPath);
                    } else {
                        qDebug() << "[luwu-launcher] demo placeholder, no app to launch";
                    }
                }
                break;
            case Qt::Key_Back:
                name = "KEY_BACK(C)->BACK";
                stack.setCurrentIndex(0);
                gallery->setFocus();
                break;
            }
        }

        qDebug().noquote() << QString("[luwu-launcher][%1] key: %2 page=%3 idx=%4")
                                  .arg(QDateTime::currentMSecsSinceEpoch())
                                  .arg(name)
                                  .arg(currentPage)
                                  .arg(currentPage == 0 ? gallery->selectedIndex()
                                                        : demoGrid->selectedDemoIndex());
    };
    qApp->installEventFilter(keyFilter);

    stack.showFullScreen();
    gallery->setFocus();

    // --- 光标管理：没有鼠标时自动隐藏光标，连接后恢复 ---
    bool cursorHidden = true;
    stack.setCursor(Qt::BlankCursor);
    QTimer *cursorTimer = new QTimer(&stack);
    QObject::connect(cursorTimer, &QTimer::timeout, [&stack, &cursorHidden]() {
        bool hasMouse = isMouseConnected();
        if (hasMouse && cursorHidden) {
            stack.setCursor(Qt::ArrowCursor);
            cursorHidden = false;
        } else if (!hasMouse && !cursorHidden) {
            stack.setCursor(Qt::BlankCursor);
            cursorHidden = true;
        }
    });
    cursorTimer->start(3000);

    // --- 设备型号探测：异步调 Python 脚本，不阻塞启动 ---
    // 脚本 stdout 输出一行：xgomini / xgolite / xgomini2sw / xgorider / unknown
    // 未探测完成前 demoGrid 默认 DEV_ALL，避免删项闪烁。
    auto *detectProc = new QProcess(&stack);
    detectProc->setProcessChannelMode(QProcess::SeparateChannels);
    auto *detectTimeout = new QTimer(&stack);
    detectTimeout->setSingleShot(true);
    QObject::connect(detectTimeout, &QTimer::timeout, [detectProc]() {
        if (detectProc->state() != QProcess::NotRunning) {
            qWarning() << "[luwu-launcher] device detect timeout, killing...";
            detectProc->kill();
        }
    });
    QObject::connect(detectProc, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
        [detectProc, detectTimeout, demoGrid](int code, QProcess::ExitStatus st) {
            detectTimeout->stop();
            QByteArray out = detectProc->readAllStandardOutput().trimmed();
            QByteArray err = detectProc->readAllStandardError().trimmed();
            qDebug().noquote() << QString("[luwu-launcher] device detect finished code=%1 status=%2 out=%3")
                                      .arg(code).arg(int(st)).arg(QString::fromUtf8(out));
            if (!err.isEmpty()) {
                qDebug().noquote() << "[luwu-launcher] device detect stderr:" << QString::fromUtf8(err);
            }
            QStringList lines = QString::fromUtf8(out).split('\n', Qt::SkipEmptyParts);
            QString dev = lines.isEmpty() ? QString() : lines.last().trimmed();
            if (!dev.isEmpty() && demoGrid) {
                demoGrid->setDeviceType(dev);
            }
            detectProc->deleteLater();
        });
    detectProc->setProgram("python3");
    detectProc->setArguments({"/home/pi/luwu-os/configs/detect_device.py"});
    detectProc->start();
    detectTimeout->start(2500); // 脚本内部超时 1.5s，这里再留 1s 宽余

    // --- 语言配置文件监听：切换后自动刷新桌面/Demo 文字 ---
    auto *langWatcher = new QFileSystemWatcher(&stack);
    const QString langIniPath = QStringLiteral("/home/pi/luwu-os/configs/language.ini");
    if (QFile::exists(langIniPath)) {
        langWatcher->addPath(langIniPath);
    }
    QObject::connect(langWatcher, &QFileSystemWatcher::fileChanged,
                     [langWatcher, gallery, demoGrid, langIniPath]() {
        qDebug() << "[luwu-launcher] language.ini changed, retranslating...";
        // 部分编辑器会原子替换文件，导致 path 从 watcher 移除，需重新添加
        if (!langWatcher->files().contains(langIniPath) && QFile::exists(langIniPath)) {
            langWatcher->addPath(langIniPath);
        }
        if (gallery) gallery->retranslate();
        if (demoGrid) demoGrid->retranslate();
    });

    // --- 设备配置文件监听：device.ini 变更后自动重建 demo 列表 ---
    // 开源用户可手动 echo xgorider > configs/device.ini 即时验证 Rider 视图
    auto *devWatcher = new QFileSystemWatcher(&stack);
    const QString devIniPath = QStringLiteral("/home/pi/luwu-os/configs/device.ini");
    if (QFile::exists(devIniPath)) {
        devWatcher->addPath(devIniPath);
    }
    QObject::connect(devWatcher, &QFileSystemWatcher::fileChanged,
                     [devWatcher, demoGrid, devIniPath]() {
        qDebug() << "[luwu-launcher] device.ini changed, reloading device type...";
        if (!devWatcher->files().contains(devIniPath) && QFile::exists(devIniPath)) {
            devWatcher->addPath(devIniPath);
        }
        QFile f(devIniPath);
        if (f.open(QIODevice::ReadOnly | QIODevice::Text)) {
            QString dev = QString::fromUtf8(f.readLine()).trimmed().toLower();
            f.close();
            if (demoGrid && !dev.isEmpty()) {
                demoGrid->setDeviceType(dev);
            }
        }
    });

    // --- 顶部状态栏覆盖层（时间 + 电量）---
    auto *statusBar = new StatusBar(&stack);
    // 延迟 200ms 确保 linuxfb 全屏尺寸已就绪后再定位
    QTimer::singleShot(200, [&stack, statusBar]() {
        statusBar->setGeometry(0, 0, stack.width(), 26);
        statusBar->raise();
        statusBar->show();
    });
    // 每次切换页面后重新置顶
    QObject::connect(&stack, &QStackedWidget::currentChanged, [statusBar]() {
        statusBar->raise();
    });
    // GalleryView 动画期间，每帧都置顶并刷新 StatusBar。
    // 原因：卡片 centerY=25 与 StatusBar 底边(y=25)重叠，动画 setGeometry 会触发
    // GalleryView 背景重绘，在 linuxfb 上会短暂覆盖 StatusBar 区域造成黑字闪烁。
    QObject::connect(gallery->animationTimer(), &QTimer::timeout, statusBar, [statusBar]() {
        statusBar->raise();
        statusBar->update();
    });

    int rc = app.exec();

    if (preloadProc->state() != QProcess::NotRunning) {
        preloadProc->terminate();
        preloadProc->waitForFinished(1000);
    }
    unlink(FIFO_PATH);
    return rc;
}
