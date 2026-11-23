#include "profile_store.h"
#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLockFile>
#include <QTemporaryDir>
#include <QtTest>

using namespace cdng;
namespace {
Paths paths(const QTemporaryDir &temp) {
    return {temp.path() + "/Camera Support", temp.path() + "/Profile Backups", true};
}
void write(const QString &path, const QByteArray &bytes) {
    QDir().mkpath(QFileInfo(path).absolutePath());
    QFile file(path); QVERIFY(file.open(QIODevice::WriteOnly)); QCOMPARE(file.write(bytes), qint64(bytes.size()));
}
QByteArray read(const QString &path) { QFile file(path); if (!file.open(QIODevice::ReadOnly)) return {}; return file.readAll(); }
QString file(const Paths &p, const QString &name = ProfileFile) { return QDir(p.support).filePath(name); }
}
class ProfileTests : public QObject {
    Q_OBJECT
private slots:
    void embeddedPairIsPinned() {
        const auto bundle = Bundle::embedded(); QVERIFY2(bundle.valid(), qPrintable(bundle.error));
        QVERIFY(!Bundle::checked(bundle.profile + " ", bundle.signal).valid());
        QVERIFY(!Bundle::checked(bundle.profile, bundle.signal + " ").valid());
        auto signal = QJsonDocument::fromJson(bundle.signal).object(); signal["gen"] = 4;
        QVERIFY(!Bundle::checked(bundle.profile, QJsonDocument(signal).toJson()).valid());
    }
    void missingInstallIdempotentAndRemove() {
        QTemporaryDir temp; QVERIFY(temp.isValid()); const auto p = paths(temp); const ProfileStore store(p);
        QCOMPARE(store.inspect().state, ProfileState::Missing);
        auto op = store.install(); QVERIFY2(op.ok, qPrintable(op.message)); QVERIFY(op.changed);
        QCOMPARE(store.inspect().state, ProfileState::Ready);
        QCOMPARE(read(file(p)), Bundle::embedded().profile);
        op = store.install(); QVERIFY(op.ok); QVERIFY(!op.changed); QVERIFY(op.backup.isEmpty());
        op = store.remove(); QVERIFY(op.ok); QVERIFY(op.changed); QVERIFY(!op.backup.isEmpty());
        QCOMPARE(read(QDir(op.backup).filePath("0.json.bak")), Bundle::embedded().profile);
        QCOMPARE(store.inspect().state, ProfileState::Missing);
        op = store.remove(); QVERIFY(op.ok); QVERIFY(!op.changed);
    }
    void pocketOgInstallsAndRemovesItsResolveLuts() {
        QTemporaryDir temp; QVERIFY(temp.isValid());
        auto p = paths(temp); p.luts = temp.path() + "/LUT/cdng2braw";
        const auto bundle = Bundle::embedded(ProfileSpec::pocketOg()); QVERIFY2(bundle.valid(), qPrintable(bundle.error));
        QCOMPARE(bundle.luts.size(), 2);                                   // its own and the Sigma fp's
        const ProfileStore store(p, bundle);
        const auto luts = store.lutPaths();
        QCOMPARE(luts, QStringList({QDir(p.luts).filePath("Pocket 2013 DNG Film from DWG Intermediate.cube"),
                                    QDir(p.luts).filePath("Sigma fp to Pocket 2013 DNG Film from DWG Intermediate.cube")}));
        auto op = store.install(); QVERIFY2(op.ok, qPrintable(op.message));
        QCOMPARE(store.inspect().state, ProfileState::Ready);
        for (int i = 0; i < 2; ++i) QCOMPARE(read(luts[i]), bundle.luts[i]);
        for (const auto &lut : luts) {
            QVERIFY(QFile::remove(lut));                                   // one LUT missing: profile needs updating
            QCOMPARE(store.inspect().state, ProfileState::Update);
            op = store.install(); QVERIFY2(op.ok, qPrintable(op.message)); QCOMPARE(store.inspect().state, ProfileState::Ready);
        }
        write(luts[1], "LUT_3D_SIZE 2\n");                               // another version
        QCOMPARE(store.inspect().state, ProfileState::Update);
        QVERIFY(store.install().ok); QCOMPARE(read(luts[1]), bundle.luts[1]);
        write(QDir(p.luts).filePath("other.cube"), "x");                   // someone else's LUT in the folder survives
        op = store.remove(); QVERIFY2(op.ok, qPrintable(op.message));
        for (const auto &lut : luts) QVERIFY(!QFileInfo::exists(lut));
        QVERIFY(QFileInfo::exists(QDir(p.luts).filePath("other.cube")));
        QCOMPARE(store.inspect().state, ProfileState::Missing);
    }
    void pocketOgLutsArePinned() {
        auto bundle = Bundle::embedded(ProfileSpec::pocketOg()); QVERIFY(bundle.valid());
        QVERIFY(!Bundle::checked(bundle.profile, bundle.signal, bundle.spec, {bundle.luts[0]}).valid());
        QVERIFY(!Bundle::checked(bundle.profile, bundle.signal, bundle.spec, {bundle.luts[0], bundle.luts[1] + " "}).valid());
    }
    void withoutResolveTheProfileAloneIsReady() {
        QTemporaryDir temp; const auto p = paths(temp);                    // luts empty: Resolve not found
        const ProfileStore store(p, Bundle::embedded(ProfileSpec::pocketOg()));
        QVERIFY(store.lutPaths().isEmpty()); QVERIFY(store.install().ok); QCOMPARE(store.inspect().state, ProfileState::Ready);
    }
    void sigmaHasNoLut() {
        QTemporaryDir temp; auto p = paths(temp); p.luts = temp.path() + "/LUT/cdng2braw";
        const ProfileStore store(p); QVERIFY(store.lutPaths().isEmpty()); QVERIFY(store.install().ok);
        QVERIFY(!QFileInfo::exists(p.luts));
    }
    void legacyCorrectFilenameIsDetected() {
        QTemporaryDir temp; const auto p = paths(temp); write(file(p, LegacyProfileFile), Bundle::embedded().profile);
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Ready);
        QVERIFY(!store.install().changed); QVERIFY(!QFileInfo::exists(file(p)));
    }
    void damagedFileIsBackedUpAndRepaired() {
        QTemporaryDir temp; const auto p = paths(temp); const QByteArray damaged("{broken\n"); write(file(p), damaged);
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Damaged);
        const auto op = store.install(); QVERIFY2(op.ok, qPrintable(op.message));
        QCOMPARE(read(QDir(op.backup).filePath("0.json.bak")), damaged);
        QCOMPARE(store.inspect().state, ProfileState::Ready);
    }
    void pocketOgProfileIsEmbeddedAndIndependentOfSigma() {
        const auto og = Bundle::embedded(ProfileSpec::pocketOg());
        QVERIFY2(og.valid(), qPrintable(og.error));
        QVERIFY(Bundle::embedded().valid());
        QVERIFY(!Bundle::checked(og.profile, og.signal).valid());              // Sigma rules reject the OG files
        QVERIFY(ProfileSpec::byId("unknown").id.isEmpty());
        QCOMPARE(ProfileSpec::byId(QString()).id, QString("sigma-fp"));
        QTemporaryDir temp; QVERIFY(temp.isValid()); const auto p = paths(temp);
        const ProfileStore sigma(p), pocket(p, og);
        QCOMPARE(pocket.inspect().state, ProfileState::Missing);
        QVERIFY(sigma.install().ok);
        QCOMPARE(pocket.inspect().state, ProfileState::Missing);                // the Sigma file is not ours
        QVERIFY(pocket.install().ok);
        QCOMPARE(pocket.inspect().state, ProfileState::Ready);
        QCOMPARE(sigma.inspect().state, ProfileState::Ready);
        QVERIFY(QFileInfo::exists(file(p, "pocket-og-cinemadng-experimental.json")));
        QVERIFY(pocket.remove().ok);
        QCOMPARE(pocket.inspect().state, ProfileState::Missing);
        QCOMPARE(sigma.inspect().state, ProfileState::Ready);
        QVERIFY(adminScript("/x/helper", true, "es", "pocket-og").contains("--profile pocket-og"));
        QVERIFY(!adminScript("/x/helper", true, "es", "'; rm -rf /").contains("rm -rf"));
    }
    void incompatibleAndDuplicateProfilesAreConsolidated() {
        QTemporaryDir temp; const auto p = paths(temp); const auto bundle = Bundle::embedded();
        const auto old = bundle.profile + "\n";
        write(file(p, LegacyProfileFile), old); write(file(p, "another-name.json"), bundle.profile);
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Update);
        const auto op = store.install(); QVERIFY2(op.ok, qPrintable(op.message));
        QCOMPARE(store.inspect().state, ProfileState::Ready);
        QVERIFY(!QFileInfo::exists(file(p, LegacyProfileFile))); QVERIFY(!QFileInfo::exists(file(p, "another-name.json")));
        QCOMPARE(QDir(op.backup).entryList({"*.bak"}, QDir::Files).size(), 2);
        QVERIFY(!QDir(p.support).entryList({"*.bak"}, QDir::Files).size());
    }
    void foreignCameraIsNeverOverwrittenOrRemoved() {
        QTemporaryDir temp; const auto p = paths(temp); const QByteArray foreign("{\"cameras\":[{\"manufacturer\":\"OTHER\"}]}");
        write(file(p), foreign); const ProfileStore store(p);
        QCOMPARE(store.inspect().state, ProfileState::Conflict);
        QVERIFY(!store.install().ok); QVERIFY(!store.remove().ok); QCOMPARE(read(file(p)), foreign);
    }
    void unrelatedProfilesSurviveInstallationAndRemoval() {
        QTemporaryDir temp; const auto p = paths(temp); const QByteArray foreign("{\"cameras\":[]}");
        write(file(p, "other-camera.json"), foreign); const ProfileStore store(p);
        QVERIFY(store.install().ok); QVERIFY(store.remove().ok); QCOMPARE(read(file(p, "other-camera.json")), foreign);
    }
    void mixedCameraFileIsNotTreatedAsOurs() {
        QTemporaryDir temp; const auto p = paths(temp); auto obj = QJsonDocument::fromJson(Bundle::embedded().profile).object();
        auto cameras = obj["cameras"].toArray(); cameras.append(QJsonObject{{"manufacturer", "OTHER"}}); obj["cameras"] = cameras;
        const auto data = QJsonDocument(obj).toJson(); write(file(p, "mixed.json"), data);
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Conflict);
        QVERIFY(!store.install().ok); QCOMPARE(read(file(p, "mixed.json")), data);
    }
    void symlinkIsNotFollowed() {
#ifdef Q_OS_UNIX
        QTemporaryDir temp; const auto p = paths(temp); const auto outside = temp.path() + "/outside.json";
        write(outside, "untouched"); QDir().mkpath(p.support); QVERIFY(QFile::link(outside, file(p)));
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Conflict);
        QVERIFY(!store.install().ok); QCOMPARE(read(outside), QByteArray("untouched"));
#endif
    }
    void badDirectoryFailsWithoutChangingFiles() {
        QTemporaryDir temp; const auto p = paths(temp); write(p.support, "not a directory");
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Error);
        QVERIFY(!store.install().ok); QCOMPARE(read(p.support), QByteArray("not a directory"));
    }
    void danglingSymlinkIsAlsoRejected() {
#ifdef Q_OS_UNIX
        QTemporaryDir temp; const auto p = paths(temp); QDir().mkpath(p.support);
        const auto absent = temp.path() + "/does-not-exist.json";
        QVERIFY(QFile::link(absent, file(p)));
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Conflict);
        QVERIFY(!store.install().ok); QVERIFY(!QFileInfo::exists(absent));
#endif
    }
    void linkedSupportDirectoryIsNotModified() {
#ifdef Q_OS_UNIX
        QTemporaryDir temp; const auto p = paths(temp); const auto other = temp.path() + "/other";
        QDir().mkpath(other); QVERIFY(QFile::link(other, p.support));
        const ProfileStore store(p); QCOMPARE(store.inspect().state, ProfileState::Conflict);
        QVERIFY(!store.install().ok); QVERIFY(QDir(other).entryList(QDir::Files).isEmpty());
#endif
    }
    void failedAtomicWriteLeavesPreviousProfileIntact() {
        QTemporaryDir temp; const auto p = paths(temp); const auto old = Bundle::embedded().profile + " "; write(file(p), old);
        ProfileStore store(p, Bundle::embedded(), [](const QString &, const QByteArray &, QString *error) {
            *error = "permission denied / simulated disk full"; return false;
        });
        const auto op = store.install(); QVERIFY(!op.ok); QCOMPARE(read(file(p)), old);
        QCOMPARE(read(QDir(op.backup).filePath("0.json.bak")), old);
    }
    void realPermissionFailureDoesNotTruncateTheExistingProfile() {
#ifdef Q_OS_UNIX
        QTemporaryDir temp; const auto p = paths(temp); const auto old = Bundle::embedded().profile + " "; write(file(p), old);
        QVERIFY(QFile::setPermissions(p.support, QFileDevice::ReadOwner | QFileDevice::ExeOwner));
        if (QFileInfo(p.support).isWritable()) {
            QFile::setPermissions(p.support, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
            QSKIP("directory remains writable under this account/ACL");
        }
        const ProfileStore store(p); const auto operation = store.install();
        QVERIFY(QFile::setPermissions(p.support, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner));
        QVERIFY(!operation.ok); QCOMPARE(read(file(p)), old);
#endif
    }
    void verificationFailureRestoresLegacyAndCanonicalCopies() {
        QTemporaryDir temp; const auto p = paths(temp); const auto old = Bundle::embedded().profile + " ";
        write(file(p), old); write(file(p, LegacyProfileFile), old + "\n");
        ProfileStore store(p, Bundle::embedded(), [](const QString &path, const QByteArray &, QString *error) {
            return ProfileStore::atomicWrite(path, "{bad data", error);
        });
        const auto op = store.install(); QVERIFY(!op.ok);
        QCOMPARE(read(file(p)), old); QCOMPARE(read(file(p, LegacyProfileFile)), old + "\n");
    }
    void lockPreventsConcurrentMutation() {
        QTemporaryDir temp; const auto p = paths(temp); QDir().mkpath(p.backups);
        QLockFile lock(QDir(p.backups).filePath("profile-install.lock")); QVERIFY(lock.tryLock(0));
        const ProfileStore store(p); QVERIFY(!store.install().ok); QVERIFY(!QFileInfo::exists(file(p)));
    }
    void unsupportedPlatformDoesNotGuessPaths() {
        const ProfileStore store({{}, {}, false}); QCOMPARE(store.inspect().state, ProfileState::Unsupported);
        QVERIFY(!store.install().ok); QVERIFY(!store.remove().ok);
    }
    void damagedBundleCannotInstall() {
        QTemporaryDir temp; auto bundle = Bundle::embedded(); bundle.profile.append(' ');
        bundle = Bundle::checked(bundle.profile, bundle.signal);
        const ProfileStore store(paths(temp), bundle); QCOMPARE(store.inspect().state, ProfileState::Error);
        QVERIFY(!store.install().ok);
    }
    void shellArgumentsAreQuotedAsData() {
        QCOMPARE(shellQuote("a'b"), QString("'a'\"'\"'b'"));
        QCOMPARE(adminScript("/Applications/Normal App/helper", true),
                 QString("do shell script \"'/Applications/Normal App/helper' --install\" with administrator privileges"));
        const auto script = adminScript("/tmp/a'b\\\"$(touch nope)/helper", false);
        QVERIFY(script.contains(" --uninstall")); QVERIFY(script.contains("'\\\"'\\\"'"));
        QVERIFY(!script.contains('\n'));
    }
};
QTEST_GUILESS_MAIN(ProfileTests)
#include "profile_store_test.moc"
