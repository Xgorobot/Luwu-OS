#include "demogridview.h"
#include "devicetable.h"
#include "i18n.h"
#include <QPainter>
#include <QResizeEvent>
#include <QKeyEvent>
#include <QTimer>
#include <QDebug>
#include <QGraphicsDropShadowEffect>
#include <QStringList>

static constexpr const char *ASSET_DIR = "/home/pi/luwu-os/launcher/assets/";

// 示例程序列表：中文名 + 英文名 + 颜色 + appPath + iconFile + compat
// compat 为表达式，语法详见 demogridview.h 顶部说明：
//   "*"            → 所有机型
//   "@dog"         → dog 家族（mini / lite / mini2sw / mini3w …）
//   "@rider"       → rider 家族
//   "xgomini2sw"   → 仅该机型可见
//   "@dog,!xgolite"→ dog 家族但排除 lite
static const DemoItem DEMOS[] = {
    {"表演模式", "Performance", "#3498DB", "apps/perform/main.py",       "demo_perform.png",    "*"},
    {"图传模式", "RC Mode",     "#E74C3C", "apps/rc_mode/main.py",       "demo_rc.png",         "*"},
    {"热点模式", "Hotspot",     "#9B59B6", "apps/hotspot/main.py",       "demo_hotspot.png",    "*"},
    {"群组表演", "Group Show",  "#E67E22", "apps/group_perform/main.py", "demo_group.png",      "*"},
    {"手势识别", "Gesture",     "#FF6B6B", "apps/gesture/main.py",       "demo_gesture.png",    "*"},
    {"人脸跟随", "Face Follow", "#4A90D9", "apps/face_follow/main.py",   "demo_face_track.png", "*,!xgorider"},
    {"小球抓取", "Ball Catch",  "#50C878", "apps/ball_catch/main.py",    "demo_ball_track.png", "@dog,!xgorider"},
    {"手柄控制", "Gamepad",     "#FFD93D", "apps/gamepad/main.py",        "demo_gamepad.png",    "*"},
    {"雷达扫描", "Radar",       "#2ECC71", "apps/radar/main.py",         "demo_radar.png",      "*"},
};
static constexpr int DEMO_TOTAL = sizeof(DEMOS) / sizeof(DEMOS[0]);

// 查表：设备 id → 家族名；未知 id 返回空串
static QString familyOf(const QString &deviceId) {
    if (deviceId.isEmpty()) return QString();
    QByteArray idUtf8 = deviceId.toUtf8();
    for (int i = 0; i < DEVICE_COUNT; ++i) {
        if (idUtf8 == DEVICES[i].id) {
            return QString::fromUtf8(DEVICES[i].family);
        }
    }
    return QString();
}

// 表达式匹配：该 demo 在 deviceId 上是否可见。
// deviceId 为空（未探测）或 未知 时保守返回 true（避免误判隐藏）。
static bool matchCompat(const char *rule, const QString &deviceId) {
    if (!rule || !*rule) return true;            // 未填则似同 "*"
    if (deviceId.isEmpty()) return true;          // 未探测：全部显示
    QString family = familyOf(deviceId);
    if (family.isEmpty()) return true;            // 未知机型：全部显示

    QStringList tokens = QString::fromUtf8(rule).split(',', Qt::SkipEmptyParts);
    bool included = false;
    for (QString tok : tokens) {
        tok = tok.trimmed();
        if (tok.isEmpty()) continue;
        if (tok == "*") {
            included = true;
        } else if (tok.startsWith('@')) {
            if (tok.mid(1) == family) included = true;
        } else if (tok.startsWith('!')) {
            if (tok.mid(1) == deviceId) return false;   // 黑名单优先否决
        } else {
            if (tok == deviceId) included = true;
        }
    }
    return included;
}

// 启动自检矩阵：在控制台打印每个 demo 在每个已注册机型上的可见性，
// 方便本地调试 / 开源用户验证。仅在首次调用时输出一次。
static void logCompatMatrixOnce() {
    static bool printed = false;
    if (printed) return;
    printed = true;
    QString header = QStringLiteral("[DemoCompat] demo \\ device :");
    for (int d = 0; d < DEVICE_COUNT; ++d) {
        header += " " + QString::fromUtf8(DEVICES[d].id);
    }
    qDebug().noquote() << header;
    for (int i = 0; i < DEMO_TOTAL; ++i) {
        QString row = QStringLiteral("[DemoCompat] %1 [%2] :")
                          .arg(QString::fromUtf8(DEMOS[i].nameEn))
                          .arg(QString::fromUtf8(DEMOS[i].compat));
        for (int d = 0; d < DEVICE_COUNT; ++d) {
            bool ok = matchCompat(DEMOS[i].compat, QString::fromUtf8(DEVICES[d].id));
            row += ok ? " V" : " -";
        }
        qDebug().noquote() << row;
    }
}

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

    // 默认未探测：全部显示（探测完成后再 setDeviceType 重建）
    rebuildVisibleItems();

    QTimer::singleShot(30, this, [this]() {
        loadImages();
        updateItemPositions();
        updateSelectionStyle();
        updateCornerPositions();
    });
}

void DemoGridView::rebuildVisibleItems() {
    // 输出一次兼容性矩阵供调试
    logCompatMatrixOnce();

    // 记录当前选中项的 appPath，重建后尽量保持选中
    QString prevPath;
    if (selectedIdx >= 0 && selectedIdx < demoItems.size()) {
        prevPath = QString::fromUtf8(demoItems[selectedIdx].appPath);
    }

    // 清理旧 widgets
    for (auto *lbl : itemIcons) {
        if (lbl) lbl->deleteLater();
    }
    for (auto *lbl : itemLabels) {
        if (lbl) lbl->deleteLater();
    }
    itemIcons.clear();
    itemLabels.clear();
    demoItems.clear();

    // 按 currentDeviceId 与 compat 表达式过滤 DEMOS[]
    for (int i = 0; i < DEMO_TOTAL; ++i) {
        if (!matchCompat(DEMOS[i].compat, currentDeviceId)) continue;
        demoItems.append(DEMOS[i]);

        auto *icon = new QLabel(this);
        icon->setAlignment(Qt::AlignCenter);
        icon->setScaledContents(true);
        icon->setAttribute(Qt::WA_TransparentForMouseEvents);
        icon->setStyleSheet("background: transparent;");
        icon->show();
        itemIcons.append(icon);

        auto *lbl = new QLabel(luwu::tr(QString::fromUtf8(DEMOS[i].name),
                                        QString::fromUtf8(DEMOS[i].nameEn)), this);
        lbl->setAlignment(Qt::AlignCenter);
        lbl->setAttribute(Qt::WA_TransparentForMouseEvents);
        lbl->setStyleSheet("color: #1a3a6e; font-size: 11px; background: transparent;");
        lbl->show();
        itemLabels.append(lbl);
    }

    // 恢复选中：优先按 appPath，否则 clamp
    selectedIdx = 0;
    if (!prevPath.isEmpty()) {
        for (int i = 0; i < demoItems.size(); ++i) {
            if (QString::fromUtf8(demoItems[i].appPath) == prevPath) {
                selectedIdx = i;
                break;
            }
        }
    }
    if (demoItems.isEmpty()) {
        selectedIdx = 0;
        currentPage = 0;
    } else {
        if (selectedIdx >= demoItems.size()) selectedIdx = demoItems.size() - 1;
        currentPage = selectedIdx / ITEMS_PER_PAGE;
    }
}

void DemoGridView::setDeviceType(const QString &devType) {
    QString id = devType.trimmed().toLower();
    if (id == currentDeviceId) return;
    currentDeviceId = id;
    qDebug() << "[DemoGridView] setDeviceType:" << devType
             << " family=" << familyOf(id);

    rebuildVisibleItems();
    loadImages();
    updateItemPositions();
    updateSelectionStyle();
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
    for (int i = 0; i < demoItems.size(); ++i) {
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
        if (!demoItems.isEmpty()) {
            emit demoEntered(QString::fromUtf8(demoItems[selectedIdx].appPath));
        }
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

    int total = demoItems.size();
    if (total == 0) return;

    int cols = COLUMNS;
    // 间距
    int gapX = 18;
    int gapY = 16;

    // 当前页的起止索引
    int startItem = currentPage * ITEMS_PER_PAGE;
    int endItem = qMin(startItem + ITEMS_PER_PAGE, total);
    int pageCount = endItem - startItem;
    int rows = (pageCount + cols - 1) / cols;
    if (rows <= 0) rows = 1;

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

    for (int i = 0; i < total; ++i) {
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
            // 选中项 label 加宽加高以容纳 14px 大字，保持水平居中
            int extraW = 24;
            int extraH = 6;
            itemLabels[i]->setGeometry(ix - extraW / 2, iy + itemH + 8 - extraH / 2, itemW + extraW, labelH + extraH);
        } else {
            itemIcons[i]->setGeometry(ix, iy, itemW, itemH);
            itemLabels[i]->setGeometry(ix, iy + itemH + 8, itemW, labelH);
        }
        itemIcons[i]->setVisible(true);
        itemLabels[i]->setVisible(true);
    }
}

void DemoGridView::updateSelectionStyle() {
    int total = demoItems.size();
    for (int i = 0; i < total; ++i) {
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
                f.setWeight(QFont::Medium);
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
    if (demoItems.isEmpty()) return QString();
    if (selectedIdx < 0 || selectedIdx >= demoItems.size()) return QString();
    return QString::fromUtf8(demoItems[selectedIdx].appPath);
}

void DemoGridView::moveSelection(int delta) {
    int total = demoItems.size();
    if (total <= 0) return;
    int newIdx = selectedIdx + delta;
    if (newIdx < 0) newIdx = total - 1;
    if (newIdx >= total) newIdx = 0;
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
