#pragma once
#include "profile_store.h"
#include <QWidget>

class QLabel;
class QPushButton;
class QProcess;

namespace cdng {
class ProfileWindow : public QWidget {
    ProfileStore store_;
    QLabel *lut_ = nullptr, *status_;
    QLabel *detail_;
    QLabel *resolve_;
    QPushButton *install_;
    QPushButton *remove_;
    QPushButton *refresh_;
    QProcess *process_ = nullptr;
    void *elevated_ = nullptr;   // Windows: proceso elevado (HANDLE) que instala o desinstala
    QProcess *probe_ = nullptr;  // Windows: --probe-camera-support, ¿lee Resolve el perfil?
    void runOperation(bool install);
    void runElevated(bool install);
    void probeResolve();
public:
    explicit ProfileWindow(ProfileStore store = ProfileStore(), QWidget *parent = nullptr);
    void refresh();
    void offerInstall();
    bool operationInProgress() const { return process_ != nullptr || elevated_ != nullptr; }
protected:
    void closeEvent(QCloseEvent *event) override;
};
}
