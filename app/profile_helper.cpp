#include "profile_store.h"
#include "i18n.h"
#include <QCoreApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <cstdio>

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    const auto args = app.arguments();
    // cdng-profile-helper --install|--uninstall [--language en|es] [--profile <embedded id>]
    QString action, language = "en", profile;
    bool valid = args.size() >= 2 && (args[1] == "--install" || args[1] == "--uninstall");
    if (valid) action = args[1];
    for (int i = 2; valid && i < args.size(); i += 2) {
        if (i + 1 >= args.size()) { valid = false; break; }
        if (args[i] == "--language" && (args[i + 1] == "en" || args[i + 1] == "es")) language = args[i + 1];
        else if (args[i] == "--profile" && !cdng::ProfileSpec::byId(args[i + 1]).id.isEmpty() && profile.isEmpty()) profile = args[i + 1];
        else valid = false;
    }
    if (!valid) {
        std::fputs("Expected --install or --uninstall [--language en|es] [--profile ID]; system paths and embedded calibrations only.\n", stderr);
        return 2;
    }
    cdng::initializeLanguage(language);
    const cdng::ProfileStore store(cdng::Paths::system(), cdng::Bundle::embedded(cdng::ProfileSpec::byId(profile)));
    const auto operation = action == "--install" ? store.install() : store.remove();
    const auto output = QJsonDocument(QJsonObject{{"ok", operation.ok}, {"changed", operation.changed},
        {"message", operation.message}, {"backup", operation.backup}}).toJson(QJsonDocument::Compact);
    std::fwrite(output.constData(), 1, static_cast<size_t>(output.size()), stdout);
    std::fputc('\n', stdout);
    // Private RPC transport: a completed request returns JSON even when the
    // operation failed. Otherwise osascript discards stdout on a nonzero exit.
    // Authorization/launch failures still arrive through osascript's status.
    return 0;
}
