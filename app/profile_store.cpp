#include "i18n.h"
#include "profile_store.h"

#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLockFile>
#include <QSaveFile>
#include <QSettings>
#include <QUuid>
#include <algorithm>
#include <utility>
#include <vector>

static void profileResources() {
    static const bool initialized = [] { Q_INIT_RESOURCE(profiles); return true; }();
    Q_UNUSED(initialized);
}

namespace cdng {
namespace {
QString digest(const QByteArray &bytes) {
    return QString::fromLatin1(QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex());
}
QJsonObject json(const QByteArray &bytes, bool *ok) {
    QJsonParseError error;
    const auto doc = QJsonDocument::fromJson(bytes, &error);
    *ok = error.error == QJsonParseError::NoError && doc.isObject();
    return doc.object();
}
bool cameraMatches(const QJsonObject &camera, const ProfileSpec &spec, qint64 revision) {
    return camera.value("manufacturer").toString() == spec.manufacturer &&
           camera.value("camera_model").toString() == spec.model &&
           camera.value("revision").toDouble() == revision;
}
bool owns(const QJsonObject &profile, const ProfileSpec &spec, qint64 revision) {
    const auto cameras = profile.value("cameras").toArray();
    return cameras.size() == 1 && cameraMatches(cameras[0].toObject(), spec, revision);
}
bool containsOwnCamera(const QJsonObject &profile, const ProfileSpec &spec, qint64 revision) {
    for (const auto camera : profile.value("cameras").toArray())
        if (cameraMatches(camera.toObject(), spec, revision)) return true;
    return false;
}
// Every file the bundle installs: the current revision first, then its companions.
struct Entry { QString file, legacyFile; qint64 revision; QString sha; QByteArray content; };
QList<Entry> entries(const Bundle &bundle) {
    QList<Entry> list{{bundle.spec.file, bundle.spec.legacyFile, bundle.spec.revision, bundle.spec.profileSha, bundle.profile}};
    for (qsizetype i = 0; i < bundle.spec.companions.size(); ++i) {
        const auto &c = bundle.spec.companions[i];
        list.append({c.file, {}, c.revision, c.sha, i < bundle.companions.size() ? bundle.companions[i] : QByteArray()});
    }
    return list;
}
bool noSymlinks(QString path, QString *error) {
    path = QDir::cleanPath(QFileInfo(path).absoluteFilePath());
    for (;;) {
        const QFileInfo info(path);
        if (info.isSymLink()) {
            *error = t("The path contains a symbolic link and was not changed: ") + path;
            return false;
        }
        const auto parent = info.absolutePath();
        if (parent == path) break;
        path = parent;
    }
    return true;
}
bool readFile(const QString &path, QByteArray *data, QString *error) {
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) { *error = file.errorString(); return false; }
    if (file.size() > 4 * 1024 * 1024) { *error = t("The profile exceeds the supported size."); return false; }
    *data = file.readAll();
    if (file.error() != QFile::NoError) { *error = file.errorString(); return false; }
    return true;
}
bool knownName(const QString &name, const QList<Entry> &wanted) {
    for (const auto &e : wanted) if (name == e.file || (!e.legacyFile.isEmpty() && name == e.legacyFile)) return true;
    return false;
}
struct Snapshot { QString path; QByteArray data; };
}

ProfileSpec ProfileSpec::sigma() {
    ProfileSpec s;
    s.id = "sigma-fp"; s.displayName = "Sigma fp"; s.manufacturer = "SIGMA"; s.model = "fp"; s.cameraType = "SIGMA fp";
    s.file = ProfileFile; s.legacyFile = LegacyProfileFile;
    s.profileResource = ":/profiles/sigma_fp_cinemadng_camera_support.json";
    s.signalResource = ":/profiles/sigma_fp_cinemadng_signal.json";
    s.signalProfileFilename = "sigma_fp_cinemadng_camera_support.json";
    s.profileSha = ProfileSha; s.signalSha = SignalSha;
    s.scope = "Experimental calibration: Sigma fp, ISO 800; 3000–10000 K, tint −30…30. The computer receiving the BRAW also needs the profile.";
    return s;
}
ProfileSpec ProfileSpec::pocketOg() {
    ProfileSpec s;
    s.id = "pocket-og"; s.displayName = "Blackmagic Pocket Cinema Camera (2013)";
    s.manufacturer = "Blackmagic"; s.model = "Pocket Cinema Camera"; s.cameraType = "Blackmagic Pocket Cinema Camera";
    // Revision with the ISO table (ISO 200–1600 as decode-time gain); the converter writes it.
    s.file = "pocket-og-cinemadng-iso.json";
    s.revision = 1128878927;
    s.profileResource = ":/profiles/pocket_og_cinemadng_iso_camera_support.json";
    s.signalResource = ":/profiles/pocket_og_cinemadng_signal.json";
    s.signalProfileFilename = "pocket_og_cinemadng_iso_camera_support.json";
    s.profileSha = "5344084f2ab960c6595a9c886f94ac52ade86d07c6c6a14b190877980c29dee6";
    s.signalSha = "30cd14b01b6052cb187131779d49de3f27ccb02c97b6c764a1b3cba93910f5c2";
    // The calibrated revision BRAW files written by earlier versions name; same colour and signal, single ISO.
    s.companions = {{"pocket-og-cinemadng-experimental.json", ":/profiles/pocket_og_cinemadng_camera_support.json",
                     "63a0358740bfc98a459769a68d5490345785d1cbba42aa7086c6fe192d62fb5c", 1128549959}};
    s.scope = "Experimental calibration made from a real take: original Pocket Cinema Camera CinemaDNG, ISO 200–1600; 3000–10000 K, tint −30…30. The computer receiving the BRAW also needs the profile.";
    // DaVinci WG/Intermediate → the DNG's «Blackmagic Design» look (Film Gen 1), measured in Resolve.
    // Sigma fp in DaVinci WG/Intermediate → the same look: a matrix fitted on a shared scene, then the LUT above.
    s.luts = {{":/profiles/pocket_og_dng_film_from_dwg_intermediate.cube", "Pocket 2013 DNG Film from DWG Intermediate.cube",
               "5fbd4bae5f28a29ad8c89892d77516e08bda8eaf40402c21cc7bed33d12e0069"},
              {":/profiles/sigma_fp_to_pocket_og_dng_film_from_dwg_intermediate.cube",
               "Sigma fp to Pocket 2013 DNG Film from DWG Intermediate.cube",
               "a2f98b210fbaf23f44d91da5b1bc52ac0efea6b324b567982d8248498a4866cb"}};
    return s;
}
QList<ProfileSpec> ProfileSpec::all() { return {sigma(), pocketOg()}; }
ProfileSpec ProfileSpec::byId(const QString &id) {
    if (id.isEmpty()) return sigma();
    for (const auto &spec : all()) if (spec.id == id) return spec;
    return {};
}
Bundle Bundle::checked(QByteArray profile, QByteArray signal, const ProfileSpec &spec, QList<QByteArray> luts,
                       QList<QByteArray> companions) {
    Bundle result{std::move(profile), std::move(signal), {}, spec, std::move(luts), std::move(companions)};
    bool lutsOk = result.luts.size() == spec.luts.size();
    for (qsizetype i = 0; lutsOk && i < spec.luts.size(); ++i) lutsOk = digest(result.luts[i]) == spec.luts[i].sha;
    bool profileOk, signalOk;
    const auto p = json(result.profile, &profileOk), s = json(result.signal, &signalOk);
    // Companions: pinned, owned under their own revision, and the ones the signal calibration declares as legacy.
    bool companionsOk = result.companions.size() == spec.companions.size();
    QList<qint64> legacy;
    for (const auto revision : s.value("legacy_revisions").toArray()) legacy << qint64(revision.toDouble());
    for (qsizetype i = 0; companionsOk && i < spec.companions.size(); ++i) {
        bool ok;
        const auto c = json(result.companions[i], &ok);
        const auto &companion = spec.companions[i];
        companionsOk = ok && owns(c, spec, companion.revision) && digest(result.companions[i]) == companion.sha &&
                       companion.revision != spec.revision && legacy.contains(companion.revision);
    }
    if (spec.id.isEmpty() || !profileOk || !signalOk || !owns(p, spec, spec.revision) || !companionsOk ||
        s.value("revision").toDouble() != spec.revision || s.value("gen").toInt() != spec.gen ||
        s.value("camera_type").toString() != spec.cameraType ||
        s.value("profile_filename").toString() != spec.signalProfileFilename ||
        s.value("profile_sha256").toString() != spec.profileSha || digest(result.profile) != spec.profileSha ||
        digest(result.signal) != spec.signalSha || !lutsOk) {
        result.error = t("The app's calibration resources are incompatible or damaged.");
    }
    return result;
}
Bundle Bundle::embedded(const ProfileSpec &spec) {
    profileResources();
    QFile p(spec.profileResource);
    QFile s(spec.signalResource);
    QList<QByteArray> luts, companions;
    for (const auto &lut : spec.luts) {
        QFile l(lut.resource);
        if (!l.open(QIODevice::ReadOnly)) return {{}, {}, t("Could not read the calibration resources."), spec};
        luts << l.readAll();
    }
    for (const auto &companion : spec.companions) {
        QFile c(companion.resource);
        if (!c.open(QIODevice::ReadOnly)) return {{}, {}, t("Could not read the calibration resources."), spec};
        companions << c.readAll();
    }
    if (spec.id.isEmpty() || !p.open(QIODevice::ReadOnly) || !s.open(QIODevice::ReadOnly))
        return {{}, {}, t("Could not read the calibration resources."), spec};
    return checked(p.readAll(), s.readAll(), spec, luts, companions);
}
Paths Paths::system() {
#ifdef Q_OS_MAC
    return {"/Library/Application Support/Blackmagic Design/Blackmagic RAW/Camera Support",
            "/Library/Application Support/CDNG Converter/Profile Backups", true,   // nombre antiguo: conserva las copias ya hechas
            resolveLuts()};
#elif defined(Q_OS_WIN)
    // El BlackmagicRawAPI.dll de Resolve compone %PROGRAMFILES(X86)% + «Blackmagic Design/» + «Blackmagic
    // RAW/Camera Support» (las tres cadenas están en la DLL, y con un proceso de diagnóstico que intercepta sus
    // FindFirstFileExW/CreateFileW se vio que lista y lee justo esa carpeta). Comprobado en Resolve 21.1 Studio:
    // con el perfil ahí abre los BRAW de la Pocket; bajo ProgramData no. Escribir ahí pide administrador, así que
    // la ventana del perfil se relanza elevada (UAC). Las copias de seguridad, bajo ProgramData.
    const QString x86 = QDir::fromNativeSeparators(qEnvironmentVariable("ProgramFiles(x86)", "C:/Program Files (x86)"));
    const QString data = QDir::fromNativeSeparators(qEnvironmentVariable("ProgramData", "C:/ProgramData"));
    return {x86 + "/Blackmagic Design/Blackmagic RAW/Camera Support",
            data + "/CDNG Converter/Profile Backups", true, resolveLuts()};
#else
    return {{}, {}, false}; // Do not invent unverified Camera Support locations.
#endif
}
QString Paths::resolveLuts() {
#ifdef Q_OS_MAC
    // Resolve's LUT folder, created by its installer; with only the app present, it is created for it.
    const QString base = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT";
    const bool installed = QFileInfo(base).isDir() || QFileInfo("/Applications/DaVinci Resolve/DaVinci Resolve.app").isDir() ||
                           QFileInfo("/Applications/DaVinci Resolve Studio/DaVinci Resolve.app").isDir();
    return installed ? base + "/cdng2braw" : QString();
#elif defined(Q_OS_WIN)
    // La carpeta de LUT de Resolve en Windows cuelga de Support (en macOS cuelga directamente de DaVinci Resolve).
    const QString data = QDir::fromNativeSeparators(qEnvironmentVariable("ProgramData", "C:/ProgramData"));
    const QString base = data + "/Blackmagic Design/DaVinci Resolve/Support/LUT";
    const bool installed = QFileInfo(base).isDir() || !resolveExecutable().isEmpty();
    return installed ? base + "/cdng2braw" : QString();
#else
    return {};
#endif
}
QString Paths::resolveExecutable() {
#ifdef Q_OS_WIN
    // Resolve no guarda su carpeta de instalación en el registro, pero sí asocia los .drp a su Resolve.exe:
    // «"C:\…\DaVinci Resolve\Resolve.exe" "%1"». Así se encuentra aunque se instalara fuera de la ruta por defecto.
    const QSettings command("HKEY_CLASSES_ROOT\\ResolveDrpFile\\shell\\open\\command", QSettings::NativeFormat);
    QString line = command.value(".").toString().trimmed();
    QString exe = line.startsWith('"') ? line.mid(1, line.indexOf('"', 1) - 1) : line.section(' ', 0, 0);
    exe = QDir::fromNativeSeparators(exe);
    if (!exe.isEmpty() && QFileInfo(exe).isFile()) return exe;
    exe = QDir::fromNativeSeparators(qEnvironmentVariable("ProgramFiles", "C:/Program Files")) +
          "/Blackmagic Design/DaVinci Resolve/Resolve.exe";
    return QFileInfo(exe).isFile() ? exe : QString();
#else
    return {};
#endif
}
bool Inspection::canInstall() const {
    return state == ProfileState::Missing || state == ProfileState::Update || state == ProfileState::Damaged;
}
bool Inspection::canRemove() const { return !files.isEmpty() && state != ProfileState::Conflict && state != ProfileState::Error; }

ProfileStore::ProfileStore(Paths paths, Bundle bundle, AtomicWriter writer)
    : paths_(std::move(paths)), bundle_(std::move(bundle)), writer_(std::move(writer)) {
    if (!writer_) writer_ = atomicWrite;
}

Inspection ProfileStore::inspect() const {
    if (!bundle_.valid()) return {ProfileState::Error, bundle_.error, {}};
    if (!paths_.supported) return {ProfileState::Unsupported,
        t("Profile installation has not been verified on this system yet."), {}};
    QString error;
    if (!noSymlinks(paths_.support, &error)) return {ProfileState::Conflict, error, {}};
    const QFileInfo directory(paths_.support);
    if (!directory.exists()) return {ProfileState::Missing, t("The camera profile must be installed."), {}};
    if (!directory.isDir() || !directory.isReadable())
        return {ProfileState::Error, t("Cannot read the Camera Support directory."), {}};
    const auto wanted = entries(bundle_);
    QStringList candidates;
    std::vector<int> found(std::size_t(wanted.size()), 0), correct(std::size_t(wanted.size()), 0);
    bool damaged = false;
    // Inspect identity/revision in all JSONs, including legacy filenames. Each revision of ours is one file.
    const auto files = QDir(paths_.support).entryInfoList(QDir::Files | QDir::Dirs | QDir::NoDotAndDotDot | QDir::Hidden | QDir::System, QDir::Name);
    for (const auto &file : files) {
        if (file.suffix().compare("json", Qt::CaseInsensitive) != 0) continue;
        if (file.isSymLink() || !file.isFile())
            return {ProfileState::Conflict, t("A profile entry is not a regular file: ") + file.fileName(), {}};
        QByteArray content;
        if (!readFile(file.absoluteFilePath(), &content, &error))
            return {ProfileState::Error, t("Cannot check ") + file.fileName() + ": " + error, {}};
        bool ok;
        const auto object = json(content, &ok);
        if (!ok) {
            if (knownName(file.fileName(), wanted)) { candidates << file.absoluteFilePath(); damaged = true; }
            continue; // Malformed profiles of other cameras are outside our scope.
        }
        qsizetype owner = -1;
        bool partlyOurs = false;
        for (qsizetype k = 0; k < wanted.size(); ++k) {
            if (owns(object, bundle_.spec, wanted[k].revision)) owner = k;
            partlyOurs = partlyOurs || containsOwnCamera(object, bundle_.spec, wanted[k].revision);
        }
        if (owner >= 0) {
            candidates << file.absoluteFilePath();
            ++found[owner];
            if (digest(content) == wanted[owner].sha) ++correct[owner];
        } else if (knownName(file.fileName(), wanted) || partlyOurs) {
            return {ProfileState::Conflict,
                t("The file ") + file.fileName() + t(" contains another calibration or multiple cameras. It will not be overwritten."), {}};
        }
    }
    if (candidates.isEmpty()) return {ProfileState::Missing, t("The camera profile must be installed."), {}};
    bool complete = candidates.size() == wanted.size(), missingRevision = false, extra = false;
    for (qsizetype k = 0; k < wanted.size(); ++k) {
        complete = complete && found[k] == 1 && correct[k] == 1;
        missingRevision = missingRevision || found[k] == 0;
        extra = extra || found[k] > 1 || found[k] != correct[k];
    }
    if (complete) {
        for (const auto &lut : lutPaths()) if (!noSymlinks(lut, &error)) return {ProfileState::Conflict, error, {}};
        if (!lutCurrent()) return {ProfileState::Update, t("The DaVinci Resolve LUT that goes with the profile is missing or outdated."), candidates};
        return {ProfileState::Ready, t("The installed file matches this app's calibration."), candidates};
    }
    if (damaged) return {ProfileState::Damaged, t("The profile is damaged. It can be repaired while keeping a backup."), candidates};
    if (missingRevision && !extra)
        return {ProfileState::Update, t("A revision of our profile is missing. Installing adds it and keeps the one already installed."), candidates};
    return {ProfileState::Update, t("Another version of our profile or duplicate copies were found. A backup will be kept when updating."), candidates};
}

bool ProfileStore::atomicWrite(const QString &path, const QByteArray &content, QString *error) {
    QSaveFile file(path);
    file.setDirectWriteFallback(false);
    if (!file.open(QIODevice::WriteOnly)) { *error = file.errorString(); return false; }
    const auto permissions = QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ReadGroup | QFileDevice::ReadOther;
    if (!file.setPermissions(permissions) || file.write(content) != content.size()) {
        *error = file.errorString(); file.cancelWriting(); return false;
    }
    if (!file.commit()) { *error = file.errorString(); return false; }
    return true;
}

QStringList ProfileStore::lutPaths() const {
    QStringList paths;
    if (!paths_.luts.isEmpty()) for (const auto &lut : bundle_.spec.luts) paths << QDir(paths_.luts).filePath(lut.file);
    return paths;
}
bool ProfileStore::lutCurrent() const {
    const auto paths = lutPaths();   // empty: Resolve not found, or a camera without LUTs
    for (qsizetype i = 0; i < paths.size(); ++i) {
        QByteArray content; QString error;
        if (!QFileInfo(paths[i]).isFile() || QFileInfo(paths[i]).isSymLink() || !readFile(paths[i], &content, &error) ||
            digest(content) != bundle_.spec.luts[i].sha) return false;
    }
    return true;
}
bool ProfileStore::removeLuts(QString *error) const {
    for (const auto &lut : lutPaths()) {
        if (!QFileInfo::exists(lut)) continue;
        if (!noSymlinks(lut, error)) return false;
        if (!QFile::remove(lut)) { *error = t("Could not remove the DaVinci Resolve LUT."); return false; }
    }
    if (!paths_.luts.isEmpty()) QDir().rmdir(paths_.luts);   // only if empty
    return true;
}

Operation ProfileStore::change(bool install) const {
    auto current = inspect();
    QString lutError;
    if (install && current.state == ProfileState::Ready) return {true, false, t("The profile is already installed."), {}};
    if (!install && current.state == ProfileState::Missing) {
        bool hadLut = false;
        for (const auto &lut : lutPaths()) hadLut = hadLut || QFileInfo::exists(lut);
        if (!removeLuts(&lutError)) return {false, false, lutError, {}};
        return {true, hadLut, t("The profile is already uninstalled."), {}};
    }
    if ((install && !current.canInstall()) || (!install && !current.canRemove()))
        return {false, false, current.detail, {}};
    QString error;
    if (!noSymlinks(paths_.backups, &error) || !QDir().mkpath(paths_.backups))
        return {false, false, error.isEmpty() ? t("Cannot create the backup folder. Check permissions.") : error, {}};
    QLockFile lock(QDir(paths_.backups).filePath("profile-install.lock"));   // shared by every camera profile
    lock.setStaleLockTime(30000);
    if (!lock.tryLock(0)) return {false, false, t("Another profile operation is in progress."), {}};
    current = inspect(); // Re-read under lock; do not use a stale GUI inspection.
    if (install && current.state == ProfileState::Ready) return {true, false, t("The profile is already installed."), {}};
    if (!install && current.state == ProfileState::Missing) return {true, false, t("The profile is already uninstalled."), {}};
    if ((install && !current.canInstall()) || (!install && !current.canRemove()))
        return {false, false, current.detail, {}};
    if (!noSymlinks(paths_.support, &error) || !QDir().mkpath(paths_.support))
        return {false, false, error.isEmpty() ? t("Cannot write to Camera Support. Check permissions.") : error, {}};
    QList<Snapshot> originals;
    for (const auto &path : current.files) {
        QByteArray bytes;
        if (!noSymlinks(path, &error) || !readFile(path, &bytes, &error)) return {false, false, error, {}};
        originals.append({path, bytes});
    }
    const auto wanted = entries(bundle_);
    QStringList destinations;
    for (const auto &e : wanted) destinations << QDir(paths_.support).filePath(e.file);
    for (const auto &destination : destinations)
        if (!noSymlinks(destination, &error)) return {false, false, error, {}};
    QString backup;
    if (!originals.isEmpty()) {
        backup = QDir(paths_.backups).filePath(QDateTime::currentDateTimeUtc().toString("yyyyMMddTHHmmsszzz") + "-" + QUuid::createUuid().toString(QUuid::WithoutBraces));
        if (!QDir().mkpath(backup)) return {false, false, t("Cannot create the backup."), {}};
        QJsonArray manifest;
        for (int i = 0; i < originals.size(); ++i) {
            const auto name = QString::number(i) + ".json.bak";
            if (!atomicWrite(QDir(backup).filePath(name), originals[i].data, &error))
                return {false, false, t("The profile was not changed: backup failed. ") + error, backup};
            manifest.append(QJsonObject{{"file", name}, {"original_name", QFileInfo(originals[i].path).fileName()},
                                         {"sha256", digest(originals[i].data)}});
        }
        const auto receipt = QJsonDocument(QJsonObject{{"files", manifest}, {"action", install ? "install" : "uninstall"}}).toJson();
        if (!atomicWrite(QDir(backup).filePath("manifest.json"), receipt, &error))
            return {false, false, t("The profile was not changed: writing the backup record failed. ") + error, backup};
    }
    auto isOriginal = [&](const QString &path) {
        return std::any_of(originals.begin(), originals.end(), [&](const Snapshot &o) { return o.path == path; });
    };
    auto rollback = [&]() {
        bool ok = true;
        QString restoreError;
        for (const auto &original : originals)
            if (!atomicWrite(original.path, original.data, &restoreError)) ok = false;
        for (const auto &destination : destinations)
            if (!isOriginal(destination) && QFileInfo::exists(destination) && !QFile::remove(destination)) ok = false;
        return ok;
    };
    // Recheck after making backups, before the first mutation. Cooperating
    // installers are serialized by QLockFile; unrelated edits must be preserved.
    for (const auto &original : originals) {
        QByteArray latest;
        if (!noSymlinks(original.path, &error) || !readFile(original.path, &latest, &error) || latest != original.data)
            return {false, false, t("The profile changed during the operation. Check it again."), backup};
    }
    for (const auto &destination : destinations) {
        if (!noSymlinks(destination, &error)) return {false, false, error, backup};
        if (install && !isOriginal(destination) && QFileInfo::exists(destination))
            return {false, false, t("Another profile file appeared. It will not be overwritten."), backup};
    }
    for (qsizetype k = 0; install && k < wanted.size(); ++k) {
        if (writer_(destinations[k], wanted[k].content, &error)) continue;
        // AtomicWriter's contract: failure leaves its destination unchanged. Before the first write nothing changed;
        // after it, the files already written are taken back.
        if (k == 0) return {false, false, t("Could not install the profile: ") + error + t(" The previous state was preserved."), backup};
        const bool restored = rollback();
        return {false, false, t("Could not install the profile: ") + error + " " +
            (restored ? t("The previous state was restored.") : t("Restore the profile from the backup.")), backup};
    }
    for (const auto &original : originals) {
        if (install && destinations.contains(original.path)) continue;
        if (!QFile::remove(original.path)) {
            const bool restored = rollback();
            return {false, false, restored ? t("The operation failed and the previous state was restored.")
                                          : t("The operation failed; restore the profile from the backup."), backup};
        }
    }
    // The LUTs go into our own folder inside Resolve's: nothing of anyone else's is replaced there.
    const auto luts = lutPaths();
    bool lutsWritten = !install || luts.isEmpty() || lutCurrent() || (noSymlinks(paths_.luts, &lutError) && QDir().mkpath(paths_.luts));
    for (qsizetype i = 0; install && lutsWritten && i < luts.size(); ++i) lutsWritten = writer_(luts[i], bundle_.luts[i], &lutError);
    if (!lutsWritten) {
        const bool restored = rollback();
        return {false, false, t("Could not install the DaVinci Resolve LUT: ") + lutError + " " +
            (restored ? t("The previous state was restored.") : t("Restore the profile from the backup.")), backup};
    }
    if (!install && !removeLuts(&lutError)) return {false, false, lutError, backup};
    const auto after = inspect();
    if (after.state != (install ? ProfileState::Ready : ProfileState::Missing)) {
        const bool restored = rollback();
        return {false, false, restored ? t("Final verification failed and the previous state was restored.")
                                      : t("Verification failed; restore the profile from the backup."), backup};
    }
    const QString lutNote = luts.isEmpty() ? QString()
        : " " + (install ? QString(t("The LUTs for DaVinci Resolve are in its LUT folder: %1.")).arg("cdng2braw") : t("Its DaVinci Resolve LUTs were removed too."));
    return {true, true, (install ? t("Profile installed and verified on disk.") : t("Only our profile was uninstalled.")) + lutNote, backup};
}

QString stateName(ProfileState state) {
    switch (state) {
    case ProfileState::Missing: return "missing";
    case ProfileState::Ready: return "ready";
    case ProfileState::Update: return "update";
    case ProfileState::Damaged: return "damaged";
    case ProfileState::Conflict: return "conflict";
    case ProfileState::Error: return "error";
    case ProfileState::Unsupported: return "unsupported";
    }
    return "error";
}
QString shellQuote(const QString &value) {
    auto quoted = value;
    quoted.replace("'", "'\"'\"'");
    return "'" + quoted + "'";
}
QString adminScript(const QString &helper, bool install, const QString &language, const QString &profile) {
    auto command = shellQuote(helper) + (install ? " --install" : " --uninstall");
    if (!language.isEmpty()) command += " --language " + QString(language == "es" ? "es" : "en");
    // Only identifiers of embedded calibrations reach the shell (never paths or user text).
    if (!profile.isEmpty() && !ProfileSpec::byId(profile).id.isEmpty()) command += " --profile " + ProfileSpec::byId(profile).id;
    command.replace("\\", "\\\\"); command.replace("\"", "\\\"");
    return "do shell script \"" + command + "\" with administrator privileges";
}
}
