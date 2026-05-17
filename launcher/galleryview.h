#pragma once
#include <QWidget>
#include <QLabel>
#include <QTimer>
#include <QVector>
#include <QRect>
#include <QPixmap>

// ========================================================================
// 配置常量（与 GalleryView 耦合的部分）
// ========================================================================
static constexpr int CARD_COUNT = 5;
static constexpr int VISIBLE_RANGE = 1;
static constexpr int ANIM_DURATION_MS = 220;
static constexpr int ANIM_TICK_MS = 20;

struct CardData {
    const char *title;     // 中文标题
    const char *titleEn;   // 英文标题
    const char *imageFile;
    const char *appPath;
};

extern const CardData CARDS[CARD_COUNT];

// ========================================================================
// GalleryView: 3-visible card carousel
// ========================================================================
class GalleryView : public QWidget {
    Q_OBJECT
public:
    explicit GalleryView(QWidget *parent = nullptr);
    ~GalleryView() override = default;

    void loadImages();
    int selectedIndex() const;
    QString selectedAppPath() const;
    void moveSelection(int delta);
    bool isAnimating() const;
    void retranslate();  // 重新根据当前语言设置卡片文字

signals:
    void enterPressed(int index);

protected:
    void resizeEvent(QResizeEvent *) override;
    void keyPressEvent(QKeyEvent *ev) override;

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

    void updateTargetStates();
    void updateCardStyles();
    void applyCardGeometry(int i);
    void updateCornerPositions();
    void onAnimTick();
};
