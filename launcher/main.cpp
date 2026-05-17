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
#include <sys/stat.h>
#include <unistd.h>
#include "keyfilter.h"
#include "galleryview.h"
#include "demogridview.h"
#include "statusbar.h"

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

    // --- 页面栈 ---
    QStackedWidget stack;
    stack.setWindowTitle("Luwu OS");

    GalleryView *gallery = new GalleryView(&stack);
    DemoGridView *demoGrid = new DemoGridView(&stack);

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

    int rc = app.exec();

    if (preloadProc->state() != QProcess::NotRunning) {
        preloadProc->terminate();
        preloadProc->waitForFinished(1000);
    }
    unlink(FIFO_PATH);
    return rc;
}
