#include "profile_window.h"
#include "i18n.h"
#include <QApplication>
#include <QAbstractButton>
#include <QLabel>
#include <QMessageBox>
#include <QPushButton>
#include <QTemporaryDir>
#include <QTimer>
#include <QtTest>

using namespace cdng;
class ProfileWindowTests : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() { initializeLanguage("es"); }
    void fileVerificationNeverClaimsResolveHasLoadedIt() {
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        QVERIFY(store.install().ok);
        ProfileWindow window(store);
        QCOMPARE(window.findChild<QLabel *>("profileStatus")->text(), QString("Perfil instalado · archivo verificado"));
        QVERIFY(window.findChild<QLabel *>("resolveStatus")->text().contains("sin verificar"));
        QVERIFY(!window.findChild<QPushButton *>("installProfile")->isEnabled());
    }
    void decliningStartupPopupDoesNotInstallAnything() {
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        ProfileWindow window(store);
        QVERIFY(window.findChild<QPushButton *>("installProfile")->isEnabled());
        bool sawPopup = false;
        QTimer::singleShot(0, &window, [&] {
            auto *box = qobject_cast<QMessageBox *>(QApplication::activeModalWidget());
            QVERIFY(box); sawPopup = true;
            for (auto *button : box->buttons()) if (box->buttonRole(button) == QMessageBox::RejectRole) {
                button->click(); return;
            }
            QFAIL("missing cancel button");
        });
        window.offerInstall();
        QVERIFY(sawPopup);
        QCOMPARE(store.inspect().state, ProfileState::Missing);
    }
};
QTEST_MAIN(ProfileWindowTests)
#include "profile_window_test.moc"
