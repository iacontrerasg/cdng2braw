#include "i18n.h"
#include "profile_window.h"
#include <QCloseEvent>
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QFont>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QMessageBox>
#include <QProcess>
#include <QPushButton>
#include <QVBoxLayout>
#ifdef Q_OS_WIN
#include <QDateTime>
#include <QFile>
#include <QJsonArray>
#include <QTimer>
#include <qt_windows.h>
#include <shellapi.h>
#endif

namespace cdng {
ProfileWindow::ProfileWindow(ProfileStore store, QWidget *parent) : QWidget(parent), store_(std::move(store)) {
    setWindowTitle(t("CDNG to BRAW Converter — Camera profile"));
    resize(640, 390);
    setMinimumWidth(560);
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(28, 26, 28, 26);
    layout->setSpacing(14);
    auto *title = new QLabel(QString(t("%1 camera profile")).arg(store_.spec().displayName), this);
    auto font = title->font(); font.setPointSize(font.pointSize() + 6); font.setBold(true); title->setFont(font);
    layout->addWidget(title);
    auto *intro = new QLabel(t("Install the calibration to edit this converter's BRAW with the CinemaDNG temperature and tint response."), this);
    intro->setWordWrap(true); layout->addWidget(intro);
    status_ = new QLabel(this);
    status_->setObjectName("profileStatus");
    font = status_->font(); font.setBold(true); status_->setFont(font);
    layout->addWidget(status_);
    detail_ = new QLabel(this); detail_->setWordWrap(true); layout->addWidget(detail_);
    resolve_ = new QLabel(t("Resolve loading: unverified. If Resolve was open when the profile was installed, save the project and restart Resolve."), this);
    resolve_->setObjectName("resolveStatus");
    resolve_->setWordWrap(true); layout->addWidget(resolve_);
    if (!store_.spec().luts.isEmpty()) {
        lut_ = new QLabel(this); lut_->setObjectName("lutStatus"); lut_->setWordWrap(true); layout->addWidget(lut_);
    }
    auto *scope = new QLabel(t(store_.spec().scope.toUtf8().constData()), this);
    scope->setWordWrap(true); layout->addWidget(scope);
    layout->addStretch();
    auto *buttons = new QHBoxLayout;
    refresh_ = new QPushButton(t("Check again"), this);
    install_ = new QPushButton(t("Install profile"), this); install_->setDefault(true);
    install_->setObjectName("installProfile");
    remove_ = new QPushButton(t("Uninstall…"), this);
    buttons->addWidget(refresh_); buttons->addStretch(); buttons->addWidget(remove_); buttons->addWidget(install_);
    layout->addLayout(buttons);
    connect(refresh_, &QPushButton::clicked, this, &ProfileWindow::refresh);
    connect(install_, &QPushButton::clicked, this, &ProfileWindow::offerInstall);
    connect(remove_, &QPushButton::clicked, this, [this] {
        const auto answer = QMessageBox::question(this, t("Uninstall profile"),
            t("BRAW made with this calibration needs the profile to develop. Uninstall only our profile? A backup will be kept."),
            QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel);
        if (answer == QMessageBox::Yes) runOperation(false);
    });
    refresh();
}
void ProfileWindow::refresh() {
    const auto state = store_.inspect();
    QString text;
    switch (state.state) {
    case ProfileState::Ready: text = t("Profile installed · file verified"); break;
    case ProfileState::Missing: text = t("Profile installation required"); break;
    case ProfileState::Update: text = t("Profile update required"); break;
    case ProfileState::Damaged: text = t("Damaged profile"); break;
    case ProfileState::Conflict: text = t("Review the existing installation"); break;
    case ProfileState::Unsupported: text = t("Installation pending for this system"); break;
    default: text = t("Could not check the profile"); break;
    }
    status_->setText(text); detail_->setText(state.detail);
    if (lut_) lut_->setText(store_.lutPaths().isEmpty()
        ? t("DaVinci Resolve was not found: its LUTs will be installed with the profile once Resolve is installed.")
        : QString(t("LUTs for DaVinci Resolve (DaVinci WG/Intermediate to the DNG's Blackmagic Design look, from this camera and from the Sigma fp): %1 · in the LUT list, folder %2.")).arg(
              store_.lutCurrent() ? t("installed") : t("not installed"), "cdng2braw"));
    const bool idle = !operationInProgress();
    install_->setText(state.state == ProfileState::Damaged ? t("Repair profile") :
                      state.state == ProfileState::Update ? t("Update profile") : t("Install profile"));
    install_->setEnabled(idle && state.canInstall());
    remove_->setEnabled(idle && state.canRemove()); refresh_->setEnabled(idle);
#ifdef Q_OS_WIN
    if (idle && state.state == ProfileState::Ready && store_.paths().support == Paths::system().support) probeResolve();
#endif
}
#ifdef Q_OS_WIN
// Comprueba con el decodificador BRAW de Resolve (su propia DLL, en un proceso aparte) que lee nuestro fichero de
// Camera Support. Protege de un cambio de ruta en otra versión del SDK: sin esto solo se sabe que el fichero está.
void ProfileWindow::probeResolve() {
    if (probe_) return;
    probe_ = new QProcess(this);
    auto *process = probe_;
    connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError error) {
        if (error != QProcess::FailedToStart) return;
        probe_ = nullptr; process->deleteLater();
    });
    connect(process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, [this, process] {
        const auto result = QJsonDocument::fromJson(process->readAllStandardOutput()).object();
        probe_ = nullptr; process->deleteLater();
        if (result.value("dll").toString().isEmpty() || !result.value("error").toString().isEmpty()) return;  // sin Resolve
        const auto expected = QDir::cleanPath(store_.paths().support + "/" + store_.spec().file);
        bool read = false;
        for (const auto &path : result.value("read").toArray())
            read = read || QDir::cleanPath(path.toString()).compare(expected, Qt::CaseInsensitive) == 0;
        if (read) {
            resolve_->setText(t("Resolve loading: verified. Resolve's BRAW decoder reads this profile (restart Resolve if it was open when it was installed)."));
            resolve_->setStyleSheet(QString());
        } else {
            resolve_->setText(t("Resolve loading: not detected. Resolve's BRAW decoder did not read the profile from this folder; this Resolve version may look for it elsewhere."));
            resolve_->setStyleSheet("color:#bd554c;");
        }
    });
    process->start(QCoreApplication::applicationFilePath(), {"--probe-camera-support"});
}
#endif
void ProfileWindow::offerInstall() {
    const auto state = store_.inspect();
    if (!state.canInstall() || operationInProgress()) return;
    // En macOS la instalación pide contraseña de administrador; en Windows, permiso de administrador (UAC).
#ifdef Q_OS_MAC
    const auto howText = t("To develop this converter's BRAW correctly, Resolve needs our %1 calibration.\n\nThe app will install the profile automatically and keep a backup when replacing an older version. macOS may ask for an administrator password.");
#elif defined(Q_OS_WIN)
    const auto howText = t("To develop this converter's BRAW correctly, Resolve needs our %1 calibration.\n\nThe app will install the profile automatically and keep a backup when replacing an older version. Windows will ask for administrator permission.");
#else
    const auto howText = t("To develop this converter's BRAW correctly, Resolve needs our %1 calibration.\n\nThe app will install the profile automatically and keep a backup when replacing an older version.");
#endif
    QMessageBox box(QMessageBox::Question, t("Camera profile required"),
        QString(howText).arg(store_.spec().displayName)
            + (store_.lutPaths().isEmpty() ? QString() : "\n\n" + QString(t("It also installs LUTs for DaVinci Resolve, in its LUT list under %1."))
                   .arg("cdng2braw")),
        QMessageBox::NoButton, this);
    // clickedButton() y no el valor de exec(): con botones propios, exec() devuelve un valor opaco que no es el
    // mismo en todas las plataformas (en Windows no era 0 al aceptar, así que no se instalaba nada).
    auto *accept = box.addButton(state.state == ProfileState::Missing ? t("Install profile") :
                  state.state == ProfileState::Damaged ? t("Repair profile") : t("Update profile"), QMessageBox::AcceptRole);
    box.addButton(t("Not now"), QMessageBox::RejectRole);
    box.exec();
    if (box.clickedButton() == accept) runOperation(true);
}
void ProfileWindow::runOperation(bool install) {
#ifdef Q_OS_MAC
    if (store_.paths().support != Paths::system().support || store_.paths().backups != Paths::system().backups) {
        QMessageBox::critical(this, t("Destination not allowed"), t("The installer only supports the system Camera Support location."));
        return;
    }
    const auto helper = QDir(QCoreApplication::applicationDirPath()).filePath("cdng-profile-helper");
    if (!QFileInfo(helper).isExecutable()) {
        QMessageBox::critical(this, t("Incomplete installation"), t("The installer included in the app could not be found."));
        return;
    }
    process_ = new QProcess(this);
    auto *process = process_;
    connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError error) {
        if (error != QProcess::FailedToStart) return;
        process_ = nullptr; process->deleteLater(); refresh();
        QMessageBox::critical(this, t("Could not start the installer"), t("You can try again. The profile was not changed."));
    });
    connect(process, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this,
        [this, process, install](int code, QProcess::ExitStatus exitStatus) {
            const auto output = process->readAllStandardOutput();
            const auto error = process->readAllStandardError();
            process_ = nullptr; process->deleteLater(); refresh();
            if (error.contains("(-128)")) {
                QMessageBox::information(this, t("Operation cancelled"), t("The operation was not authorised. You can try again."));
                return;
            }
            QJsonParseError parseError;
            const auto doc = QJsonDocument::fromJson(output, &parseError);
            const auto result = doc.object();
            const auto state = store_.inspect();
            const bool verified = state.state == (install ? ProfileState::Ready : ProfileState::Missing);
            if (code != 0 || exitStatus != QProcess::NormalExit || parseError.error != QJsonParseError::NoError ||
                !result.value("ok").toBool() || !verified) {
                QMessageBox box(QMessageBox::Critical, t("The operation did not complete"),
                    result.value("message").toString(t("Could not complete or verify the operation. Check permissions and try again.")),
                    QMessageBox::Ok, this);
                box.setDetailedText(QString::fromUtf8(error) + "\n" + result.value("backup").toString()); box.exec();
                return;
            }
            resolve_->setText(t("Resolve loading: pending. Save the project and restart Resolve to apply the change."));
            QMessageBox box(QMessageBox::Information, install ? t("Profile installed") : t("Profile uninstalled"),
                result.value("message").toString() + t("\n\nSave the project and restart Resolve. The app will not close it automatically."), QMessageBox::Ok, this);
            if (!result.value("backup").toString().isEmpty()) box.setDetailedText(t("Backup: ") + result.value("backup").toString());
            box.exec();
        });
    refresh();
    status_->setText(t("Waiting for macOS authorisation…"));
    process->start("/usr/bin/osascript", {"-e", adminScript(helper, install, currentLanguage(), store_.spec().id)});
#else
#ifdef Q_OS_WIN
    // La carpeta Camera Support del sistema está bajo Program Files (x86) y pide administrador: la app se relanza
    // elevada (UAC) con --install-profile/--uninstall-profile y deja el resultado en un fichero, como el helper de
    // macOS. Otras carpetas (pruebas, automatización) se escriben en el mismo proceso.
    if (store_.paths().support == Paths::system().support) { runElevated(install); return; }
#endif
    status_->setText(install ? t("Installing…") : t("Uninstalling…"));
    const auto result = install ? store_.install() : store_.remove();
    const auto state = store_.inspect();
    const bool verified = state.state == (install ? ProfileState::Ready : ProfileState::Missing);
    refresh();
    if (!result.ok || !verified) {
        QMessageBox box(QMessageBox::Critical, t("The operation did not complete"),
            result.message.isEmpty() ? t("Could not complete or verify the operation. Check permissions and try again.") : result.message,
            QMessageBox::Ok, this);
        if (!result.backup.isEmpty()) box.setDetailedText(t("Backup: ") + result.backup);
        box.exec();
        return;
    }
    resolve_->setText(t("Resolve loading: pending. Save the project and restart Resolve to apply the change."));
    QMessageBox box(QMessageBox::Information, install ? t("Profile installed") : t("Profile uninstalled"),
        result.message + t("\n\nSave the project and restart Resolve. The app will not close it automatically."), QMessageBox::Ok, this);
    if (!result.backup.isEmpty()) box.setDetailedText(t("Backup: ") + result.backup);
    box.exec();
#endif
}
#ifdef Q_OS_WIN
void ProfileWindow::runElevated(bool install) {
    const auto resultFile = QDir::temp().filePath(QString("cdng-profile-%1-%2.json")
        .arg(QCoreApplication::applicationPid()).arg(QDateTime::currentMSecsSinceEpoch()));
    QFile::remove(resultFile);
    const QStringList args{install ? "--install-profile" : "--uninstall-profile", store_.spec().id,
                           "--language", currentLanguage(), "--result", QDir::toNativeSeparators(resultFile)};
    QString parameters;
    for (const auto &arg : args) parameters += (parameters.isEmpty() ? "\"" : " \"") + arg + "\"";
    const auto exe = QDir::toNativeSeparators(QCoreApplication::applicationFilePath()).toStdWString();
    const auto params = parameters.toStdWString();
    SHELLEXECUTEINFOW info{};
    info.cbSize = sizeof info;
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC;
    info.hwnd = reinterpret_cast<HWND>(window()->winId());
    info.lpVerb = L"runas";
    info.lpFile = exe.c_str();
    info.lpParameters = params.c_str();
    info.nShow = SW_HIDE;
    status_->setText(t("Waiting for Windows authorisation…"));
    if (!ShellExecuteExW(&info) || !info.hProcess) {
        const auto error = GetLastError();
        refresh();
        if (error == ERROR_CANCELLED)
            QMessageBox::information(this, t("Operation cancelled"), t("The operation was not authorised. You can try again."));
        else
            QMessageBox::critical(this, t("Could not start the installer"), t("You can try again. The profile was not changed."));
        return;
    }
    elevated_ = info.hProcess;
    refresh();
    status_->setText(install ? t("Installing…") : t("Uninstalling…"));
    auto *timer = new QTimer(this);
    connect(timer, &QTimer::timeout, this, [this, timer, resultFile, install] {
        if (WaitForSingleObject(elevated_, 0) == WAIT_TIMEOUT) return;
        timer->stop(); timer->deleteLater();
        DWORD code = 1;
        GetExitCodeProcess(elevated_, &code);
        CloseHandle(elevated_); elevated_ = nullptr;
        QByteArray output;
        if (QFile file(resultFile); file.open(QIODevice::ReadOnly)) output = file.readAll();
        QFile::remove(resultFile);
        refresh();
        QJsonParseError parseError;
        const auto result = QJsonDocument::fromJson(output, &parseError).object();
        const auto state = store_.inspect();
        const bool verified = state.state == (install ? ProfileState::Ready : ProfileState::Missing);
        if (code != 0 || parseError.error != QJsonParseError::NoError || !result.value("ok").toBool() || !verified) {
            QMessageBox box(QMessageBox::Critical, t("The operation did not complete"),
                result.value("message").toString(t("Could not complete or verify the operation. Check permissions and try again.")),
                QMessageBox::Ok, this);
            if (!result.value("backup").toString().isEmpty()) box.setDetailedText(t("Backup: ") + result.value("backup").toString());
            box.exec();
            return;
        }
        resolve_->setText(t("Resolve loading: pending. Save the project and restart Resolve to apply the change."));
        QMessageBox box(QMessageBox::Information, install ? t("Profile installed") : t("Profile uninstalled"),
            result.value("message").toString() + t("\n\nSave the project and restart Resolve. The app will not close it automatically."),
            QMessageBox::Ok, this);
        if (!result.value("backup").toString().isEmpty()) box.setDetailedText(t("Backup: ") + result.value("backup").toString());
        box.exec();
    });
    timer->start(100);
}
#endif
void ProfileWindow::closeEvent(QCloseEvent *event) {
    if (operationInProgress()) {
        QMessageBox::information(this, t("Operation in progress"), t("Complete or cancel system authorisation before closing the application."));
        event->ignore(); return;
    }
    QWidget::closeEvent(event);
}
}
