#include "demogridview.h"
#include <QPainter>
#include <QResizeEvent>
#include <QKeyEvent>
#include <QTimer>
#include <QDebug>

static constexpr const char *ASSET_DIR = "/home/pi/luwu-os/launcher/assets/";

// 示例程序列表：名字 + 颜色 + appPath
static const DemoItem DEMOS[] = {
    // 第 1 个：图传模式（有真实图标 demo_rc.png）
    {"\u56fe\u4f20\u6a21\u5f0f", "#E74C3C", "apps/rc_mode/main.py"},
    {"Calculator", "#4A90D9", ""},
    {"Clock",      "#50C878", ""},
    {"Weather",    "#FF6B6B", ""},
    {"Notes",      "#FFD93D", ""},
    {"Music",      "#9B59B6", ""},
    {"Gallery",    "#E67E22", ""},
};
static constexpr int DEMO_COUNT = sizeof(DEMOS) / sizeof(DEMOS[0]);

// 用 QPainter 生成带颜色的圆角占位图标
static QPixmap makePlaceholderIcon(const QColor &color, int size) {
    QPixmap pix(size, size);
    pix.fill(Qt::transparent);
    {
        QPainter p(&pix);
        p.setRenderHint(QPainter::Antialiasing);

        // 圆角背景
        int margin = 5;
        p.setBrush(color);
        p.setPen(Qt::NoPen);
        p.drawRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, 14, 14);

        // 白色简笔画 — 不同 demo 画不同的小图标
        p.setPen(QPen(Qt::white, 2.5));
        p.setBrush(Qt::NoBrush);
        int cx = size / 2;
        int cy = size / 2;
        p.drawEllipse(QPoint(cx, cy - 2), 8, 8);       // 小圆
        p.drawLine(cx, cy - 16, cx, cy - 24);           // 竖线
        p.drawLine(cx - 10, cy + 16, cx + 10, cy + 16); // 横线
        p.end();
    }
    return pix;
}

// ========================================================================
// DemoGridView 实现
// ========================================================================
DemoGridView::DemoGridView(QWidget *parent)
    : QWidget(parent)
{
    setStyleSheet("background-color: #0a0a1a;");
    setFocusPolicy(Qt::StrongFocus);

    // 背景图
    bgLabel = new QLabel(this);
    bgLabel->setAttribute(Qt::WA_TransparentForMouseEvents);
    bgLabel->lower();

    // 四角图标
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

    // 创建 demo 项
    for (int i = 0; i < DEMO_COUNT; ++i) {
        demoItems.append(DEMOS[i]);

        auto *icon = new QLabel(this);
        icon->setAlignment(Qt::AlignCenter);
        icon->setScaledContents(true);
        icon->setAttribute(Qt::WA_TransparentForMouseEvents);
        icon->setStyleSheet("background: transparent;");
        itemIcons.append(icon);

        auto *lbl = new QLabel(DEMOS[i].name, this);
        lbl->setAlignment(Qt::AlignCenter);
        lbl->setAttribute(Qt::WA_TransparentForMouseEvents);
        lbl->setStyleSheet("color: #cccccc; font-size: 12px; background: transparent;");
        itemLabels.append(lbl);
    }

    selectedIdx = 0;

    QTimer::singleShot(30, this, [this]() {
        loadImages();
        updateItemPositions();
        updateSelectionStyle();
        updateCornerPositions();
    });
}

void DemoGridView::loadImages() {
    // 背景图
    QString bgPath = QString(ASSET_DIR) + "bg_macos.png";
    QPixmap bgPix(bgPath);
    if (!bgPix.isNull()) {
        bgLabel->setPixmap(bgPix);
        bgLabel->setScaledContents(true);
    }

    // 加载图标（优先真实 PNG，否则占位生成）
    for (int i = 0; i < DEMO_COUNT; ++i) {
        QPixmap icon;
        // 第 0 项（图传模式）使用真实图标
        if (i == 0) {
            icon = QPixmap(QString(ASSET_DIR) + "demo_rc.png");
        }
        if (icon.isNull()) {
            QColor color(demoItems[i].color);
            icon = makePlaceholderIcon(color, itemW);
        }
        itemIcons[i]->setPixmap(icon);
    }

    // 四角图标
    auto loadCornerIcon = [&](QLabel *label, const QString &file) {
        QPixmap pix(QString(ASSET_DIR) + file);
        if (!pix.isNull()) label->setPixmap(pix);
    };
    loadCornerIcon(cornerTL, "icon_left.png");
    loadCornerIcon(cornerTR, "icon_right.png");
    loadCornerIcon(cornerBL, "icon_back.png");
    loadCornerIcon(cornerBR, "icon_enter.png");
}

void DemoGridView::resizeEvent(QResizeEvent *) {
    bgLabel->setGeometry(0, 0, width(), height());
    updateCornerPositions();
    updateItemPositions();
}

void DemoGridView::keyPressEvent(QKeyEvent *ev) {
    switch (ev->key()) {
    case Qt::Key_Left:
        moveSelection(-1);
        break;
    case Qt::Key_Right:
        moveSelection(1);
        break;
    case Qt::Key_Return:
        emit demoEntered(demoItems[selectedIdx].appPath);
        break;
    case Qt::Key_Back:
        emit backPressed();
        break;
    default:
        break;
    }
}

void DemoGridView::updateCornerPositions() {
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

void DemoGridView::updateItemPositions() {
    int w = width();
    int h = height();
    if (w == 0 || h == 0) return;

    int cols = COLUMNS;
    int rows = (DEMO_COUNT + cols - 1) / cols;

    // 间距
    int gapX = 12;
    int gapY = 8;

    // 计算总网格宽高（注意：icon 和 label 之间有 2px 间隙）
    int totalW = cols * itemW + (cols - 1) * gapX;
    int totalH = rows * (itemH + 2 + labelH) + (rows - 1) * gapY;

    // 水平居中，左右至少留 36px 给角标区
    int startX = (w - totalW) / 2;
    if (startX < 36) startX = 36;
    // 垂直：从 topOffset 开始，在剩余空间居中
    int availH = h - topOffset - 32; // 底部留 32px 给角标
    int startY = topOffset + (availH - totalH) / 2;
    if (startY < topOffset) startY = topOffset;

    for (int i = 0; i < DEMO_COUNT; ++i) {
        int col = i % cols;
        int row = i / cols;

        int ix = startX + col * (itemW + gapX);
        int iy = startY + row * (itemH + labelH + gapY);

        itemIcons[i]->setGeometry(ix, iy, itemW, itemH);
        itemLabels[i]->setGeometry(ix, iy + itemH + 2, itemW, labelH);
    }
}

void DemoGridView::updateSelectionStyle() {
    for (int i = 0; i < DEMO_COUNT; ++i) {
        bool sel = (i == selectedIdx);
        // 选中项加白色边框和加粗大字
        if (sel) {
            itemIcons[i]->setStyleSheet(
                "QLabel { border: 3px solid #ffffff; border-radius: 10px; background: transparent; }");
            itemLabels[i]->setStyleSheet(
                "color: #ffffff; font-size: 15px; font-weight: bold; background: transparent;");
        } else {
            itemIcons[i]->setStyleSheet(
                "QLabel { border: none; background: transparent; }");
            itemLabels[i]->setStyleSheet(
                "color: #aaaaaa; font-size: 12px; background: transparent;");
        }
    }
}

QString DemoGridView::selectedDemoPath() const {
    return demoItems[selectedIdx].appPath;
}

void DemoGridView::moveSelection(int delta) {
    int cols = COLUMNS;
    int newIdx = selectedIdx + delta;
    if (newIdx < 0) newIdx = DEMO_COUNT - 1;
    if (newIdx >= DEMO_COUNT) newIdx = 0;
    selectedIdx = newIdx;
    updateSelectionStyle();
}
