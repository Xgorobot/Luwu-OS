#include "i18n.h"
#include <QFile>

namespace luwu {

static constexpr const char *LANG_INI = "/home/pi/luwu-os/configs/language.ini";

QString currentLang() {
    QFile f(LANG_INI);
    if (!f.open(QIODevice::ReadOnly | QIODevice::Text)) {
        return QStringLiteral("cn");
    }
    QString v = QString::fromUtf8(f.readAll()).trimmed();
    f.close();
    if (v == QStringLiteral("en")) return v;
    return QStringLiteral("cn");
}

} // namespace luwu
