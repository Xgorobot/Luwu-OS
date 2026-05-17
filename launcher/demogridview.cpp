#include "demogridview.h"
#include "i18n.h"
#include <QPainter>
#include <QResizeEvent>
#include <QKeyEvent>
#include <QTimer>
#include <QDebug>
#include <QGraphicsDropShadowEffect>

static constexpr const char *ASSET_DIR = "/home/pi/luwu-os/launcher/assets/";

// 示例程序列表：中文名 + 英文名 + 颜色 + appPath + iconFile
static const DemoItem DEMOS[] = {
    {"表演模式", "Performance", "#3498DB", "apps/perform/main.py",       "demo_perform.png"},
    {"图传模式", "RC Mode",     "#E74C3C", "apps/rc_mode/main.py",       "demo_rc.png"},
    {"热点模式", "Hotspot",     "#9B59B6", "apps/hotspot/main.py",       "demo_hotspot.png"},
    {"群组表演", "Group Show",  "#E67E22", "apps/group_perform/main.py", "demo_group.png"},
    {"手势识别", "Gesture",     "#FF6B6B", "apps/gesture/main.py",       "demo_gesture.png"},
    {"人脸跟随", "Face Follow", "#4A90D9", "apps/face_follow/main.py",   "demo_face_track.png"},
    {"小球抓取", "Ball Catch",  "#50C878", "apps/ball_catch/main.py",    "demo_ball_track.png"},
    {"手柄控制", "Joystick",    "#FFD93D", "apps/joystick/main.py",      "demo_gamepad.png"},
    {"雷达扫描", "Radar",       "#2ECC71", "apps/radar/main.py",         "demo_radar.png"},
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

        auto *lbl = new QLabel(luwu::tr(QString::fromUtf8(DEMOS[i].name),
                                        QString::fromUtf8(DEMOS[i].nameEn)), this);
        lbl->setAlignment(Qt::AlignCenter);
        lbl->setAttribute(Qt::WA_TransparentForMouseEvents);
        lbl->setStyleSheet("color: #1a3a6e; font-size: 11px; background: transparent;");
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

void DemoGridView::retranslate() {
    for (int i = 0; i < itemLabels.size() && i < demoItems.size(); ++i) {
        if (itemLabels[i]) {
            itemLabels[i]->setText(luwu::tr(QString::fromUtf8(demoItems[i].name),
                                            QString::fromUtf8(demoItems[i].nameEn)));
        }
    }
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
        QPixmap icon(QString(ASSET_DIR) + demoItems[i].iconFile);
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
    int margin = 0;

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
    // 间距
    int gapX = 18;
    int gapY = 16;

    // 当前页的起止索引
    int startItem = currentPage * ITEMS_PER_PAGE;
    int endItem = qMin(startItem + ITEMS_PER_PAGE, DEMO_COUNT);
    int pageCount = endItem - startItem;
    int rows = (pageCount + cols - 1) / cols;

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
        int pageIdx = i - startItem;
        if (pageIdx < 0 || pageIdx >= ITEMS_PER_PAGE) {
            // 非当前页，隐藏
            itemIcons[i]->setVisible(false);
            itemLabels[i]->setVisible(false);
            continue;
        }
        int col = pageIdx % cols;
        int row = pageIdx / cols;
        int ix = startX + col * (itemW + gapX);
        int iy = startY + row * (itemH + labelH + gapY);

        // 选中项放大 8px，居中对齐
        bool sel = (i == selectedIdx);
        if (sel) {
            int zoomW = itemW + 5;
            int zoomH = itemH + 5;
            int dx = (zoomW - itemW) / 2;
            int dy = (zoomH - itemH) / 2;
            itemIcons[i]->setGeometry(ix - dx, iy - dy, zoomW, zoomH);
        } else {
            itemIcons[i]->setGeometry(ix, iy, itemW, itemH);
        }
        itemLabels[i]->setGeometry(ix, iy + itemH + 8, itemW, labelH);
        itemIcons[i]->setVisible(true);
        itemLabels[i]->setVisible(true);
    }
}

void DemoGridView::updateSelectionStyle() {
    for (int i = 0; i < DEMO_COUNT; ++i) {
        bool sel = (i == selectedIdx);
        if (sel) {
            // 选中：无边框无底色，文字深蓝半粗，图标下方阴影
            itemIcons[i]->setStyleSheet(
                "QLabel { border: none; background: transparent; }");
            itemLabels[i]->setStyleSheet(
                "color: #0b1e4a; background: transparent;");
            {
                QFont f = itemLabels[i]->font();
                f.setPixelSize(14);
                f.setWeight(QFont::DemiBold);
                itemLabels[i]->setFont(f);
            }
            // 图标阴影
            auto *iconShadow = new QGraphicsDropShadowEffect(this);
            iconShadow->setBlurRadius(12);
            iconShadow->setOffset(0, 4);
            iconShadow->setColor(QColor(0, 0, 0, 80));
            itemIcons[i]->setGraphicsEffect(iconShadow);
        } else {
            // 未选中：无底色，深蓝文字，清除阴影
            itemIcons[i]->setStyleSheet(
                "QLabel { border: none; background: transparent; }");
            itemLabels[i]->setStyleSheet(
                "color: #1a3a6e; background: transparent;");
            {
                QFont f = itemLabels[i]->font();
                f.setPixelSize(11);
                f.setWeight(QFont::Normal);
                itemLabels[i]->setFont(f);
            }
            itemIcons[i]->setGraphicsEffect(nullptr);
            itemLabels[i]->setGraphicsEffect(nullptr);
        }
    }
}

QString DemoGridView::selectedDemoPath() const {
    return demoItems[selectedIdx].appPath;
}

void DemoGridView::moveSelection(int delta) {
    int newIdx = selectedIdx + delta;
    if (newIdx < 0) newIdx = DEMO_COUNT - 1;
    if (newIdx >= DEMO_COUNT) newIdx = 0;
    selectedIdx = newIdx;

    // 自动翻页：当选中项不在当前页时切换页面
    int newPage = selectedIdx / ITEMS_PER_PAGE;
    if (newPage != currentPage) {
        currentPage = newPage;
    }
    // 先重新布局（含缩放），再刷样式
    updateItemPositions();
    updateSelectionStyle();
}
