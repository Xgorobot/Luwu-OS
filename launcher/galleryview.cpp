#include "galleryview.h"
#include "i18n.h"
#include <QPainter>
#include <QResizeEvent>
#include <QKeyEvent>
#include <QDebug>
#include <QFile>
#include <cmath>

static constexpr const char *ASSET_DIR = "/home/pi/luwu-os/launcher/assets/";

const CardData CARDS[CARD_COUNT] = {
    {"网络",     "WiFi",     "card_network.png",  "apps/network/main.py"},
    {"编程",     "Coding",   "card_coding.png",   "apps/coding/main.py"},
    {"AI 对话",  "AI Chat",  "card_ai.png",       "apps/ai/main.py"},
    {"示例",     "Demo",     "card_more.png",     "apps/demo_page/main.py"},
    {"设置",     "Settings", "card_settings.png", "apps/settings/main.py"},
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
// GalleryView 实现
// ========================================================================
GalleryView::GalleryView(QWidget *parent)
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

        auto *lbl = new QLabel(luwu::tr(QString::fromUtf8(CARDS[i].title),
                                        QString::fromUtf8(CARDS[i].titleEn)), this);
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

void GalleryView::loadImages() {
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

int GalleryView::selectedIndex() const { return currentIndex; }

QString GalleryView::selectedAppPath() const {
    return QString("/home/pi/luwu-os/") + CARDS[currentIndex].appPath;
}

void GalleryView::moveSelection(int delta) {
    if (animating) return;
    int newIdx = currentIndex + delta;
    if (newIdx < 0 || newIdx >= CARD_COUNT) return;

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

    int w = width();
    if (w > 0) {
        for (int i = 0; i < CARD_COUNT; ++i) {
            if (qAbs(i - currentIndex) <= VISIBLE_RANGE && animStartGeom[i].left() <= -350) {
                const QRect &tgt = targetGeom[i];
                if (i < currentIndex) {
                    animStartGeom[i] = QRect(-tgt.width(), tgt.top(), tgt.width(), tgt.height());
                } else {
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

bool GalleryView::isAnimating() const { return animating; }

void GalleryView::retranslate() {
    for (int i = 0; i < CARD_COUNT && i < cardLabels.size(); ++i) {
        if (cardLabels[i]) {
            cardLabels[i]->setText(luwu::tr(QString::fromUtf8(CARDS[i].title),
                                            QString::fromUtf8(CARDS[i].titleEn)));
        }
    }
}

void GalleryView::resizeEvent(QResizeEvent *) {
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

void GalleryView::keyPressEvent(QKeyEvent *ev) {
    switch (ev->key()) {
    case Qt::Key_Left:  moveSelection(-1); break;
    case Qt::Key_Right: moveSelection(1);  break;
    default: break;
    }
}

void GalleryView::updateTargetStates() {
    int w = width();
    int h = height();
    if (w == 0 || h == 0) return;

    int centerSide = qMin(h * 70 / 100, w * 50 / 100);
    int sideSide   = centerSide * 70 / 100;

    int centerX = w / 2;
    int centerY = (h - centerSide) / 2 - 15;
    int sideY   = centerY + (centerSide - sideSide) / 2;

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
            cw = sideSide;
            ch = sideSide;
            cx = -sideSide + peek;
            cy = sideY;
        } else if (offset == 1) {
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

        int lblW = cw + 20;
        int lblH = 28;
        int lblX = cx + cw / 2 - lblW / 2;
        int lblY = cy + ch + 8;
        targetLabelGeom[i] = QRect(lblX, lblY, lblW, lblH);
    }
}

void GalleryView::updateCardStyles() {
    for (int i = 0; i < CARD_COUNT; ++i) {
        int dist = qAbs(i - currentIndex);
        bool selected = (dist == 0);
        bool near = (dist == 1);

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
            lblStyle = "color: #1a237e; font-size: 16px; font-weight: bold; background: transparent;";
        } else if (near) {
            lblStyle = "color: #1a237e; font-size: 14px; background: transparent;";
        } else {
            lblStyle = "color: #1a237e; font-size: 12px; background: transparent;";
        }
        cardLabels[i]->setStyleSheet(lblStyle);
    }
}

void GalleryView::applyCardGeometry(int i) {
    cardImages[i]->setGeometry(currentGeom[i]);
    cardLabels[i]->setGeometry(currentLabelGeom[i]);
}

void GalleryView::updateCornerPositions() {
    int w = width();
    int h = height();
    if (w == 0 || h == 0) return;

    int iconSize = 28;
    int margin = 0;

    cornerTL->setGeometry(margin, margin, iconSize, iconSize);
    cornerTR->setGeometry(w - iconSize - margin, margin, iconSize, iconSize);
    cornerBL->setGeometry(margin, h - iconSize - margin, iconSize, iconSize);
    cornerBR->setGeometry(w - iconSize - margin, h - iconSize - margin, iconSize, iconSize);
}

void GalleryView::onAnimTick() {
    animProgress += ANIM_TICK_MS;
    if (animProgress >= ANIM_DURATION_MS) {
        animProgress = ANIM_DURATION_MS;
        animating = false;
        animTimer->stop();

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
            continue;
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
