#pragma once

#include <QByteArray>
#include <QString>
#include <QList>
#include <QStringList>
#include <functional>

namespace cdng {
inline constexpr const char *ProfileFile = "sigma-fp-cinemadng-experimental.json";
inline constexpr const char *LegacyProfileFile = "sigma-fp-cinemadng.json";
inline constexpr const char *ProfileSha = "a296bee4933c8c6c4ee4d3257001486f7d89963f7b235cdbc5285525be6967c8";
inline constexpr const char *SignalSha = "ba41ec62eedb8058f3a96909f178f701dc271805b087d1adc2445ad8e92d6d70";
inline constexpr qint64 ProfileRevision = 1128549959;

struct ResolveLut { QString resource, file, sha; };   // a LUT for DaVinci Resolve, embedded and pinned
// An earlier revision of a camera profile installed next to the current one. A BRAW names the revision it was written
// with, so existing clips keep opening only while that revision stays in Camera Support.
struct CompanionProfile { QString file, resource, sha; qint64 revision = 0; };

// One embedded calibration per camera. The decoder pairs a clip with a profile when the clip's
// camera_type equals "<manufacturer> <camera_model>" of the profile (measured 2026-11-02).
struct ProfileSpec {
    QString id;                 // stable identifier, used by the worker and the helper (--profile)
    QString displayName;        // camera shown in the UI
    QString manufacturer, model, cameraType;
    QString file, legacyFile;   // installed file name (and an older name we also own)
    QString profileResource, signalResource, signalProfileFilename;
    QString profileSha, signalSha;
    QList<ResolveLut> luts;     // LUTs for DaVinci Resolve installed with the profile (empty: none)
    QList<CompanionProfile> companions;   // earlier revisions kept installed alongside (empty: none)
    QString scope;              // English source text, translated with t()
    qint64 revision = ProfileRevision;
    int gen = 5;
    static ProfileSpec sigma();
    static ProfileSpec pocketOg();
    static ProfileSpec byId(const QString &id);   // empty id → sigma; unknown → invalid (empty id)
    static QList<ProfileSpec> all();
};
struct Bundle {
    QByteArray profile;
    QByteArray signal;
    QString error;
    ProfileSpec spec = ProfileSpec::sigma();
    QList<QByteArray> luts;     // the content of each spec LUT, in the same order
    QList<QByteArray> companions;   // the content of each spec companion profile, in the same order
    bool valid() const { return error.isEmpty() && !profile.isEmpty(); }
    static Bundle embedded(const ProfileSpec &spec = ProfileSpec::sigma());
    static Bundle checked(QByteArray profile, QByteArray signal, const ProfileSpec &spec = ProfileSpec::sigma(), QList<QByteArray> luts = {},
                          QList<QByteArray> companions = {});
};
struct Paths {
    QString support;
    QString backups;
    bool supported = true;
    QString luts;   // our folder inside DaVinci Resolve's LUT folder; empty when Resolve is not installed
    static Paths system();
    static QString resolveLuts();
    static QString resolveExecutable();   // Windows: Resolve.exe (registro o ruta por defecto); vacío si no está
};
enum class ProfileState { Missing, Ready, Update, Damaged, Conflict, Error, Unsupported };
struct Inspection {
    ProfileState state = ProfileState::Error;
    QString detail;
    QStringList files;
    bool canInstall() const;
    bool canRemove() const;
};
struct Operation {
    bool ok = false;
    bool changed = false;
    QString message;
    QString backup;
};
// Success replaces the destination atomically; failure MUST leave it unchanged.
using AtomicWriter = std::function<bool(const QString &, const QByteArray &, QString *)>;

class ProfileStore {
    Paths paths_;
    Bundle bundle_;
    AtomicWriter writer_;
    Operation change(bool install) const;
    bool removeLuts(QString *error) const;
public:
    explicit ProfileStore(Paths paths = Paths::system(), Bundle bundle = Bundle::embedded(),
                          AtomicWriter writer = {});
    Inspection inspect() const;
    Operation install() const { return change(true); }
    Operation remove() const { return change(false); }
    const Paths &paths() const { return paths_; }
    const ProfileSpec &spec() const { return bundle_.spec; }
    QStringList lutPaths() const;   // where the Resolve LUTs go; empty without LUTs or without Resolve
    bool lutCurrent() const;        // every LUT installed and current
    static bool atomicWrite(const QString &, const QByteArray &, QString *error);
};
QString stateName(ProfileState state);
QString shellQuote(const QString &value);
QString adminScript(const QString &helper, bool install, const QString &language = {}, const QString &profile = {});
}
