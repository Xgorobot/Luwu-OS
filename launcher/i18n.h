#pragma once
// Luwu OS launcher 国际化（极简实现）
// 读取 /home/pi/luwu-os/configs/language.ini，返回 "cn" 或 "en"
// 业务侧通过 LUWU_TR(cnText, enText) 宏选择当前语言文本。
#include <QString>

namespace luwu {

// 返回当前语言代码（"cn"/"en"），读取失败默认 "cn"
QString currentLang();

// 根据当前语言挑选 cn/en 字符串
inline QString tr(const QString &cn, const QString &en) {
    return currentLang() == QStringLiteral("en") ? en : cn;
}

} // namespace luwu

#define LUWU_TR(cn, en) luwu::tr(QStringLiteral(cn), QStringLiteral(en))
