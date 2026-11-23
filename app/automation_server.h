#pragma once
// Modo de automatización (--automation): control completo de la interfaz desde fuera, para pruebas.
//
// Solo existe si la app se arranca con --automation. Escucha en un socket local (QLocalServer: Unix en macOS/Linux,
// tubería con nombre en Windows) accesible solo para el usuario, y cada petición debe llevar el token aleatorio que
// la app publica al arrancar (en stdout y en un fichero 0600). Protocolo: una línea JSON por petición
// {"id", "token", "cmd", ...} y una línea JSON por respuesta {"id", "ok", ...} o evento {"event", ...}.
// Órdenes en docs/automation.md y cliente en tools/app_control.py.
#include "profile_store.h"

#include <QJsonObject>
#include <QObject>
#include <QPointer>
#include <QSet>

class QLocalServer;
class QLocalSocket;
class QWidget;

namespace cdng {

class AutomationServer : public QObject {
    Q_OBJECT
public:
    // `name`: nombre del servidor local (vacío: uno único por proceso). `profilePaths`: carpetas de perfiles que usa
    // la ventana, para la orden `profile`.
    AutomationServer(const QString &name, Paths profilePaths, QObject *parent = nullptr);
    bool listen(QString *error);
    QString serverName() const;
    QString token() const { return token_; }
    // Fichero 0600 con {socket, token, pid} para clientes que no leen la salida estándar de la app.
    QString writeConnectionFile(QString *error) const;

private:
    void accept();
    void read(QLocalSocket *socket);
    void handle(QLocalSocket *socket, const QJsonObject &request);
    void reply(QLocalSocket *socket, QJsonObject message);
    void subscribe(QLocalSocket *socket);
    QLocalServer *server_;
    QString token_;
    Paths paths_;
    QSet<QObject *> tapped_;   // objetos (trabajos de conversión, vistas previas) ya conectados a los eventos
    QList<QPointer<QLocalSocket>> subscribers_;
};

}  // namespace cdng
