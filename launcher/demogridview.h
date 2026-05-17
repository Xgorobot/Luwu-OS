#pragma once
#include <QWidget>
#include <QLabel>
#include <QVector>
#include <QRect>

struct DemoItem {
    const char *name;     // 中文名称
    const char *nameEn;   // 英文名称
    const char *color;    // hex color for placeholder icon
    const char *appPath;  // placeholder, not used yet
    const char *iconFile; // icon image file name
};

class DemoGridView : public QWidget {
    Q_OBJECT
public:
    explicit DemoGridView(QWidget *parent = nullptr);
    ~DemoGridView() override = default;

    void loadImages();
    void moveSelection(int delta);
    int selectedDemoIndex() const { return selectedIdx; }
    QString selectedDemoPath() const;
    void retranslate();  // 重新根据当前语言设置 demo 名称

signals:
    void backPressed();
    void demoEntered(const QString &appPath);

protected:
    void resizeEvent(QResizeEvent *) override;
    void keyPressEvent(QKeyEvent *ev) override;

private:
    // 背景 + 四角图标
    QLabel *bgLabel = nullptr;
    QLabel *cornerTL = nullptr;
    QLabel *cornerTR = nullptr;
    QLabel *cornerBL = nullptr;
    QLabel *cornerBR = nullptr;

    // Demo 项
    QVector<DemoItem> demoItems;
    QVector<QLabel*> itemIcons;
    QVector<QLabel*> itemLabels;
    int selectedIdx = 0;
    int currentPage = 0;
    static constexpr int COLUMNS = 3;
    static constexpr int ITEMS_PER_PAGE = 6;  // 每页显示6个

    // 布局参数
    int itemW = 66;
    int itemH = 66;
    int labelH = 12;
    int topOffset = 36;   // 顶部留给角标区域

    void updateCornerPositions();
    void updateItemPositions();
    void updateSelectionStyle();
};
