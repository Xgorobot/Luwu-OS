#include <QApplication>
#include <QLabel>
#include <QTimer>
#include <QDateTime>
#include <QProcess>
#include <QProcessEnvironment>
#include <QDebug>
#include <QElapsedTimer>
#include <QKeyEvent>
#include <QResizeEvent>
#include <QFile>
#include <QPixmap>
#include <QPainter>
#include <cmath>
#include <sys/stat.h>
#include <unistd.h>
#include "keyfilter.h"

// ========================================================================
// 配置常量
// ========================================================================
static constexpr const char *PRELOAD_SCRIPT = "/home/pi/luwu-os/apps/demo_page/preload_app.py";
static constexpr const char *FIFO_PATH = "/tmp/luwu_preload.fifo";
static constexpr const char *ASSET_DIR = "/home/pi/luwu-os/launcher/assets/";
static constexpr int CARD_COUNT = 5;
static constexpr int VISIBLE_RANGE = 1;           // 左右各显示1张
static constexpr int ANIM_DURATION_MS = 220;
static constexpr int ANIM_TICK_MS = 20;            // ~50fps, 嵌入式友好

struct CardData {
    const char *title;
    const char *imageFile;
    const char *appPath;
};

static const CardData CARDS[CARD_COUNT] = {
    {"WiFi",       "card_network.png",  "apps/network/main.py"},
    {"Coding",     "card_coding.png",   "apps/coding/main.py"},
    {"AI Chat",    "card_ai.png",       "apps/ai/main.py"},
    {"Demo",       "card_more.png",     "apps/demo_page/main.py"},
    {"Settings",   "card_settings.png", "apps/settings/main.py"},
};

// ========================================================================
// 工具函数
// ========================================================================
static float easeOutCubic(float t) {
    return 1.0f - std::pow(1.0f - t, 3.0f);
}

static QPixmap loadCardImage(const QString &path, const QSize &fallbackSize) {
    QPixmap pix(path);
    if (!pix.isNull()) {
        return pix;
    }
    QPixmap fb(fallbackSize);
    fb.fill(QColor(30, 30, 50));
    {
        QPainter p(&fb);
        p.setPen(QColor(100, 100, 130));
        p.drawRect(10, 10, fb.width() - 21, fb.height() - 21);
        p.end();
    }
    return fb;
}

// ========================================================================
// Gallery 视图 (3-visible card carousel for 320×240 ST7789V)
// ========================================================================
class GalleryView : public QWidget {
public:
    explicit GalleryView(QWidget *parent = nullptr)
        : QWidget(parent), currentIndex(2)
    {
        setStyleSheet("background-color: #0a0a1a;");
        setFocusPolicy(Qt::StrongFocus);

        // 背景图
        bgLabel = new QLabel(this);
        bgLabel->setAttribute(Qt::WA_TransparentForMouseEvents);
        bgLabel->lower();

        // 四角功能图标
        cornerTL = new QLabel(this);
        cornerTL->setScaledContents(true);
        cornerTL->setAttribute(Qt::WA_TransparentForMouseEvents);
        cornerTL->setStyleSheet("background: transparent;");

        cornerTR = new QLabel(this);
        cornerTR->setScaledContents(true);
        cornerTR->setAttribute(Qt::WA_TransparentForMouseEvents);
        cornerTR->setStyleSheet("background: transparent;");

        cornerBL = new QLabel(this);
        cornerBL->setScaledContents(true);
        cornerBL->setAttribute(Qt::WA_TransparentForMouseEvents);
        cornerBL->setStyleSheet("background: transparent;");

        cornerBR = new QLabel(this);
        cornerBR->setScaledContents(true);
        cornerBR->setAttribute(Qt::WA_TransparentForMouseEvents);
        cornerBR->setStyleSheet("background: transparent;");

        // 5 个卡片图片 + 文字
        for (int i = 0; i < CARD_COUNT; ++i) {
            auto *img = new QLabel(this);
            img->setAlignment(Qt::AlignCenter);
            img->setScaledContents(true);
            img->setAttribute(Qt::WA_TransparentForMouseEvents);
            cardImages.append(img);

            auto *lbl = new QLabel(CARDS[i].title, this);
            lbl->setAlignment(Qt::AlignCenter);
            lbl->setAttribute(Qt::WA_TransparentForMouseEvents);
            cardLabels.append(lbl);

            currentGeom.append(QRect());
            targetGeom.append(QRect());
            currentLabelGeom.append(QRect());
            targetLabelGeom.append(QRect());
            animStartGeom.append(QRect());
            animStartLabelGeom.append(QRect());
        }

        QTimer::singleShot(50, this, [this]() {
            loadImages();
            updateTargetStates();
            for (int i = 0; i < CARD_COUNT; ++i) {
                currentGeom[i] = targetGeom[i];
                currentLabelGeom[i] = targetLabelGeom[i];
                applyCardGeometry(i);
            }
            updateCardStyles();

            // 初始隐藏范围外的卡片（默认选中index=2，左右各显示1/3，0和4隐藏）
            for (int i = 0; i < CARD_COUNT; ++i) {
                if (qAbs(i - currentIndex) > VISIBLE_RANGE) {
                    cardImages[i]->hide();
                    cardLabels[i]->hide();
                }
            }

            updateCornerPositions();
        });

        animTimer = new QTimer(this);
        QObject::connect(animTimer, &QTimer::timeout, this, &GalleryView::onAnimTick);
    }

    void loadImages() {
        QSize fallback(180, 230);
        for (int i = 0; i < CARD_COUNT; ++i) {
            QString path = QString(ASSET_DIR) + CARDS[i].imageFile;
            QPixmap pix = loadCardImage(path, fallback);
            cardImages[i]->setPixmap(pix);
        }
        // 背景图
        QString bgPath = QString(ASSET_DIR) + "bg_macos.png";
        QPixmap bgPix(bgPath);
        if (!bgPix.isNull()) {
            bgLabel->setPixmap(bgPix);
            bgLabel->setScaledContents(true);
        }

        // 四角图标：左上=左切 右上=右切 左下=返回 右下=确认
        auto loadCornerIcon = [&](QLabel *label, const QString &file) {
            QPixmap pix(QString(ASSET_DIR) + file);
            if (!pix.isNull()) label->setPixmap(pix);
        };
        loadCornerIcon(cornerTL, "icon_left.png");
        loadCornerIcon(cornerTR, "icon_right.png");
        loadCornerIcon(cornerBL, "icon_back.png");
        loadCornerIcon(cornerBR, "icon_enter.png");
    }

    int selectedIndex() const { return currentIndex; }

    QString selectedAppPath() const {
        return QString("/home/pi/luwu-os/") + CARDS[currentIndex].appPath;
    }

    void moveSelection(int delta) {
        if (animating) return;
        int newIdx = currentIndex + delta;
        if (newIdx < 0 || newIdx >= CARD_COUNT) return;

        // 先显示将要出现的卡片
        for (int i = 0; i < CARD_COUNT; ++i) {
            if (qAbs(i - newIdx) <= VISIBLE_RANGE) {
                cardImages[i]->show();
                cardLabels[i]->show();
            }
        }

        animStartGeom = currentGeom;
        animStartLabelGeom = currentLabelGeom;
        currentIndex = newIdx;
        updateTargetStates();
        updateCardStyles();

        // 修正：从屏幕外新出现的卡片，起点设为屏幕边缘外侧
        // 避免从 (-500,-500) 飞过屏幕中间造成闪动
        int w = width();
        if (w > 0) {
            for (int i = 0; i < CARD_COUNT; ++i) {
                if (qAbs(i - currentIndex) <= VISIBLE_RANGE && animStartGeom[i].left() <= -350) {
                    const QRect &tgt = targetGeom[i];
                    if (i < currentIndex) {
                        // 从左边出现 → 起点：屏幕左侧外
                        animStartGeom[i] = QRect(-tgt.width(), tgt.top(), tgt.width(), tgt.height());
                    } else {
                        // 从右边出现 → 起点：屏幕右侧外
                        animStartGeom[i] = QRect(w, tgt.top(), tgt.width(), tgt.height());
                    }
                    animStartLabelGeom[i] = targetLabelGeom[i];
                }
            }
        }

        animProgress = 0;
        animating = true;
        animTimer->start(ANIM_TICK_MS);
    }

    bool isAnimating() const { return animating; }

protected:
    void resizeEvent(QResizeEvent *) override {
        // 背景图全屏
        bgLabel->setGeometry(0, 0, width(), height());

        updateCornerPositions();
        updateTargetStates();
        if (!animating) {
            for (int i = 0; i < CARD_COUNT; ++i) {
                currentGeom[i] = targetGeom[i];
                currentLabelGeom[i] = targetLabelGeom[i];
                applyCardGeometry(i);
            }
        }
    }

    void keyPressEvent(QKeyEvent *ev) override {
        // GalleryView 自身也处理按键（双保险）
        switch (ev->key()) {
        case Qt::Key_Left:  moveSelection(-1); break;  // A 左上 → 左切
        case Qt::Key_Right: moveSelection(1);  break;  // B 右上 → 右切
        default: break;
        }
    }

private:
    QLabel *bgLabel = nullptr;
    QLabel *cornerTL = nullptr;
    QLabel *cornerTR = nullptr;
    QLabel *cornerBL = nullptr;
    QLabel *cornerBR = nullptr;
    QVector<QLabel*> cardImages;
    QVector<QLabel*> cardLabels;
    QVector<QRect> currentGeom;
    QVector<QRect> targetGeom;
    QVector<QRect> currentLabelGeom;
    QVector<QRect> targetLabelGeom;
    QVector<QRect> animStartGeom;
    QVector<QRect> animStartLabelGeom;
    int currentIndex = 2;
    QTimer *animTimer = nullptr;
    int animProgress = 0;
    bool animating = false;

    void updateTargetStates() {
        int w = width();
        int h = height();
        if (w == 0 || h == 0) return;

        // 屏幕 320×240，卡片统一做成正方形，中间大、两边只露一点
        // 中间正方形：高度占屏幕 ~85%，宽度 ~60%
        int centerSide = qMin(h * 85 / 100, w * 60 / 100);
        int sideSide   = centerSide * 78 / 100;  // 两侧卡片也是正方形，略小

        int centerX = w / 2;
        int centerY = (h - centerSide) / 2 - 8;  // 上移一点给下方文字留空间
        int sideY   = centerY + (centerSide - sideSide) / 2;  // 和中间卡垂直对齐

        // 两侧卡片只露出 ~22% 宽度
        int peek = sideSide * 28 / 100;

        for (int i = 0; i < CARD_COUNT; ++i) {
            int offset = i - currentIndex;
            int cw, ch, cx, cy;

            if (offset == 0) {
                cw = centerSide;
                ch = centerSide;
                cx = centerX - cw / 2;
                cy = centerY;
            } else if (offset == -1) {
                // 左侧卡片：大部分躲在屏幕左边外，只露出 peek 宽度
                cw = sideSide;
                ch = sideSide;
                cx = -sideSide + peek;
                cy = sideY;
            } else if (offset == 1) {
                // 右侧卡片
                cw = sideSide;
                ch = sideSide;
                cx = w - peek;
                cy = sideY;
            } else {
                targetGeom[i] = QRect(-500, -500, 1, 1);
                targetLabelGeom[i] = QRect(-500, -500, 1, 1);
                continue;
            }

            targetGeom[i] = QRect(cx, cy, cw, ch);

            // 文字标签：只给中间卡片下方放，两侧不显示
            int lblW = cw + 10;
            int lblH = 18;
            int lblX = cx + cw / 2 - lblW / 2;
            int lblY = cy + ch + 3;
            targetLabelGeom[i] = QRect(lblX, lblY, lblW, lblH);
        }
    }

    void updateCardStyles() {
        for (int i = 0; i < CARD_COUNT; ++i) {
            int dist = qAbs(i - currentIndex);
            bool selected = (dist == 0);
            bool near = (dist == 1);

            // 选中卡片：亮白边框 + 发光
            QString imgStyle;
            if (selected) {
                imgStyle = "QLabel { border: none; background-color: transparent; }";
            } else if (near) {
                imgStyle = "QLabel { border: none; background-color: transparent; }";
            } else {
                imgStyle = "QLabel { border: none; background-color: transparent; }";
            }
            cardImages[i]->setStyleSheet(imgStyle);

            QString lblStyle;
            if (selected) {
                lblStyle = "color: #ffffff; font-size: 11px; font-weight: bold; background: transparent;";
            } else if (near) {
                lblStyle = "color: #777799; font-size: 10px; background: transparent;";
            } else {
                lblStyle = "color: #333355; font-size: 9px; background: transparent;";
            }
            cardLabels[i]->setStyleSheet(lblStyle);
        }
    }

    void applyCardGeometry(int i) {
        cardImages[i]->setGeometry(currentGeom[i]);
        cardLabels[i]->setGeometry(currentLabelGeom[i]);
    }

    void updateCornerPositions() {
        int w = width();
        int h = height();
        if (w == 0 || h == 0) return;

        int iconSize = 28;
        int margin = 4;

        cornerTL->setGeometry(margin, margin, iconSize, iconSize);
        cornerTR->setGeometry(w - iconSize - margin, margin, iconSize, iconSize);
        cornerBL->setGeometry(margin, h - iconSize - margin, iconSize, iconSize);
        cornerBR->setGeometry(w - iconSize - margin, h - iconSize - margin, iconSize, iconSize);
    }

    void onAnimTick() {
        animProgress += ANIM_TICK_MS;
        if (animProgress >= ANIM_DURATION_MS) {
            animProgress = ANIM_DURATION_MS;
            animating = false;
            animTimer->stop();

            // 动画结束 → 隐藏范围外的卡片
            for (int i = 0; i < CARD_COUNT; ++i) {
                if (qAbs(i - currentIndex) > VISIBLE_RANGE) {
                    cardImages[i]->hide();
                    cardLabels[i]->hide();
                }
            }
        }
        float rawT = static_cast<float>(animProgress) / ANIM_DURATION_MS;
        float t = easeOutCubic(rawT);

        for (int i = 0; i < CARD_COUNT; ++i) {
            if (qAbs(i - currentIndex) > VISIBLE_RANGE && animStartGeom[i].left() < -100) {
                continue; // 不在可见范围内且之前就隐藏，跳过
            }
            const QRect &start = animStartGeom[i];
            const QRect &tgt = targetGeom[i];
            int x = start.left() + static_cast<int>((tgt.left() - start.left()) * t);
            int y = start.top()  + static_cast<int>((tgt.top()  - start.top())  * t);
            int w = start.width()  + static_cast<int>((tgt.width()  - start.width())  * t);
            int hh = start.height() + static_cast<int>((tgt.height() - start.height()) * t);
            currentGeom[i] = QRect(x, y, w, hh);
            cardImages[i]->setGeometry(currentGeom[i]);

            const QRect &lstart = animStartLabelGeom[i];
            const QRect &ltgt = targetLabelGeom[i];
            int lx = lstart.left() + static_cast<int>((ltgt.left() - lstart.left()) * t);
            int ly = lstart.top()  + static_cast<int>((ltgt.top()  - lstart.top())  * t);
            int lw = lstart.width()  + static_cast<int>((ltgt.width()  - lstart.width())  * t);
            int lh = lstart.height() + static_cast<int>((ltgt.height() - lstart.height()) * t);
            currentLabelGeom[i] = QRect(lx, ly, lw, lh);
            cardLabels[i]->setGeometry(currentLabelGeom[i]);
        }
    }
};

// ========================================================================
// Main
// ========================================================================
int main(int argc, char *argv[]) {
    QApplication app(argc, argv);

    GalleryView window;
    window.setWindowTitle("Luwu OS");

    // --- 预加载子进程管理 ---
    auto *preloadProc = new QProcess(&window);
    preloadProc->setProcessChannelMode(QProcess::ForwardedChannels);

    QElapsedTimer launchTimer;

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
            window.showFullScreen();
            window.setFocus();
            QTimer::singleShot(300, &window, startPreload);
        });

    auto launchApp = [&](const QString &script) {
        if (preloadProc->state() == QProcess::NotRunning) {
            qDebug() << "[luwu-launcher] preload not running, starting now...";
            startPreload();
            return;
        }
        qint64 t_req = QDateTime::currentMSecsSinceEpoch();
        qDebug().noquote() << QString("[luwu-launcher][%1] request -> trigger (%2)")
                                  .arg(t_req).arg(script);

        window.repaint();
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
            window.showFullScreen();
        }
    };

    startPreload();

    // --- 按键处理（keyfilter 做全局拦截 + GalleryView 自身也处理） ---
    auto *keyFilter = new KeyFilter(&window);
    keyFilter->onKey = [&](QKeyEvent *ke) {
        const char *name = "?";
        switch (ke->key()) {
            case Qt::Key_Left:
                // A (左上) → 左切换
                name = "KEY_LEFT(A)->LEFT";
                if (!window.isAnimating()) { window.moveSelection(-1); }
                break;
            case Qt::Key_Right:
                // B (右上) → 右切换
                name = "KEY_RIGHT(B)->RIGHT";
                if (!window.isAnimating()) { window.moveSelection(1); }
                break;
            case Qt::Key_Return:
                // D (右下) → 进入选中应用
                name = "KEY_ENTER(D)->ENTER";
                if (!window.isAnimating()) {
                    QString app = window.selectedAppPath();
                    if (QFile::exists(app)) {
                        launchApp(QString("apps/") + CARDS[window.selectedIndex()].appPath);
                    } else {
                        qDebug() << "[luwu-launcher] app not found:" << app;
                    }
                }
                break;
            case Qt::Key_Back:
                // C (左下) → 返回/退出
                name = "KEY_BACK(C)->BACK";
                break;
        }
        qDebug().noquote() << QString("[luwu-launcher][%1] key: %2 idx=%3")
                                  .arg(QDateTime::currentMSecsSinceEpoch())
                                  .arg(name)
                                  .arg(window.selectedIndex());
    };
    window.installEventFilter(keyFilter);

    window.showFullScreen();
    window.setFocus();
    int rc = app.exec();

    if (preloadProc->state() != QProcess::NotRunning) {
        preloadProc->terminate();
        preloadProc->waitForFinished(1000);
    }
    unlink(FIFO_PATH);
    return rc;
}
