#pragma once
#include <QString>
#include <QStringList>
namespace cdng {
QString languageFor(const QString &preference, const QStringList &systemUiLanguages);
void initializeLanguage(QString preference = {}, bool persist = false);
QString currentLanguage();
QString languagePreference();
QString t(const char *source);
}
