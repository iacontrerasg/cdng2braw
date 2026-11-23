#include "automation_server.h"
#include "conversion_job.h"
#include "converter_window.h"
#include "i18n.h"
#include "preview_service.h"

#include <QAbstractButton>
#include <QAbstractSlider>
#include <QAbstractSpinBox>
#include <QApplication>
#include <QBuffer>
#include <QCheckBox>
#include <QComboBox>
#include <QRadioButton>
#include <QCoreApplication>
#include <QDateTime>
#include <QMetaProperty>
#include <QDir>
#include <QDoubleSpinBox>
#include <QDropEvent>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QKeyEvent>
#include <QLabel>
#include <QLineEdit>
#include <QLocalServer>
#include <QLocalSocket>
#include <QMessageBox>
#include <QMetaObject>
#include <QMimeData>
#include <QMouseEvent>
#include <QPlainTextEdit>
#include <QProgressBar>
#include <QRandomGenerator>
#include <QSpinBox>
#include <QTextEdit>
#include <QTimer>
#include <QUrl>
#include <algorithm>
#include <functional>
#include <memory>

namespace cdng {

namespace {

struct Failure {
    QString message;
};

QList<QWidget *> windows(bool visibleOnly = true) {
    QList<QWidget *> out;
    if (auto *modal = QApplication::activeModalWidget()) out << modal;
    for (auto *w : QApplication::topLevelWidgets())
        if (!out.contains(w) && (!visibleOnly || w->isVisible())) out << w;
    return out;
}

QString segment(QWidget *w) {
    const auto siblings = w->isWindow() || !w->parentWidget()
        ? QApplication::topLevelWidgets()
        : w->parentWidget()->findChildren<QWidget *>(QString(), Qt::FindDirectChildrenOnly);
    // Con nombre único entre sus hermanos, el nombre; si no, nombre (o clase) con su índice entre los iguales.
    const QString key = w->objectName().isEmpty() ? QString(w->metaObject()->className()) : w->objectName();
    auto same = [&](QWidget *s) {
        return w->objectName().isEmpty() ? s->objectName().isEmpty() && QString(s->metaObject()->className()) == key
                                         : s->objectName() == key;
    };
    int index = 0, count = 0;
    for (auto *s : siblings) {
        if (!same(s)) continue;
        if (s == w) index = count;
        ++count;
    }
    if (!w->objectName().isEmpty() && count == 1) return key;
    return QString("%1[%2]").arg(key).arg(index);
}

// Ruta estable de un widget: nombre de objeto o Clase[índice entre hermanos sin nombre de la misma clase].
QString pathOf(QWidget *w) {
    QStringList parts;
    for (; w; w = w->parentWidget()) {
        parts.prepend(segment(w));
        if (w->isWindow()) break;
    }
    return "/" + parts.join('/');
}

QWidget *byPath(const QString &path) {
    const auto parts = path.split('/', Qt::SkipEmptyParts);
    if (parts.isEmpty()) return nullptr;
    QWidget *current = nullptr;
    for (auto *w : QApplication::topLevelWidgets()) if (segment(w) == parts.first()) { current = w; break; }
    for (int i = 1; current && i < parts.size(); ++i) {
        QWidget *next = nullptr;
        for (auto *child : current->findChildren<QWidget *>(QString(), Qt::FindDirectChildrenOnly))
            if (segment(child) == parts[i]) { next = child; break; }
        current = next;
    }
    return current;
}

QString textOf(QWidget *w) {
    if (auto *b = qobject_cast<QAbstractButton *>(w)) return b->text();
    if (auto *l = qobject_cast<QLabel *>(w)) return l->text();
    if (auto *e = qobject_cast<QLineEdit *>(w)) return e->text();
    if (auto *c = qobject_cast<QComboBox *>(w)) return c->currentText();
    if (auto *p = qobject_cast<QPlainTextEdit *>(w)) return p->toPlainText();
    if (auto *t = qobject_cast<QTextEdit *>(w)) return t->toPlainText();
    if (auto *m = qobject_cast<QMessageBox *>(w)) return m->text();
    if (w->isWindow()) return w->windowTitle();
    return {};
}

QJsonValue jsonOf(const QVariant &value) {
    switch (value.userType()) {
    case QMetaType::QSize: return QJsonArray{value.toSize().width(), value.toSize().height()};
    case QMetaType::QRect: { const auto r = value.toRect(); return QJsonArray{r.x(), r.y(), r.width(), r.height()}; }
    case QMetaType::QPoint: return QJsonArray{value.toPoint().x(), value.toPoint().y()};
    case QMetaType::QByteArray: return QString::fromLatin1(value.toByteArray().toBase64());
    default: return QJsonValue::fromVariant(value);
    }
}

QJsonObject describe(QWidget *w) {
    QJsonObject o{{"path", pathOf(w)}, {"name", w->objectName()}, {"type", w->metaObject()->className()},
                  {"enabled", w->isEnabled()}, {"visible", w->isVisible()},
                  {"geometry", QJsonArray{w->geometry().x(), w->geometry().y(), w->width(), w->height()}}};
    const auto text = textOf(w);
    if (!text.isEmpty()) o["text"] = text;
    if (auto *b = qobject_cast<QAbstractButton *>(w); b && b->isCheckable()) o["checked"] = b->isChecked();
    if (auto *c = qobject_cast<QComboBox *>(w)) {
        QJsonArray items;
        for (int i = 0; i < c->count(); ++i) items << QJsonObject{{"text", c->itemText(i)}, {"data", jsonOf(c->itemData(i))}};
        o["index"] = c->currentIndex(); o["data"] = jsonOf(c->currentData()); o["items"] = items;
    }
    if (auto *s = qobject_cast<QSpinBox *>(w)) { o["value"] = s->value(); o["range"] = QJsonArray{s->minimum(), s->maximum()}; }
    if (auto *d = qobject_cast<QDoubleSpinBox *>(w)) { o["value"] = d->value(); o["range"] = QJsonArray{d->minimum(), d->maximum()}; }
    if (auto *s = qobject_cast<QAbstractSlider *>(w)) { o["value"] = s->value(); o["range"] = QJsonArray{s->minimum(), s->maximum()}; }
    if (auto *p = qobject_cast<QProgressBar *>(w)) { o["value"] = p->value(); o["range"] = QJsonArray{p->minimum(), p->maximum()}; }
    if (w->isWindow()) { o["title"] = w->windowTitle(); o["modal"] = w->isModal(); }
    return o;
}

void collect(QWidget *w, bool all, QJsonArray &out) {
    if (!all && !w->isVisible()) return;
    out << describe(w);
    for (auto *child : w->findChildren<QWidget *>(QString(), Qt::FindDirectChildrenOnly)) collect(child, all, out);
}

// La clase o una de sus bases, con o sin espacio de nombres ("NoiseDialog" vale para cdng::NoiseDialog).
bool typeMatches(QWidget *w, const QString &type) {
    for (auto *meta = w->metaObject(); meta; meta = meta->superClass()) {
        const QString name = meta->className();
        if (name == type || name.endsWith("::" + type)) return true;
    }
    return false;
}

// Selector: "/ruta", "nombreDeObjeto" o {"name", "text", "type", "path", "index"}. Busca primero en la ventana
// modal activa y después en las ventanas visibles.
QWidget *resolve(const QJsonValue &target) {
    if (target.isString()) {
        const auto key = target.toString();
        if (key.startsWith('/')) {
            if (auto *w = byPath(key)) return w;
            throw Failure{"No widget at " + key};
        }
        QWidget *hidden = nullptr;
        for (auto *window : windows(false)) {
            if (window->objectName() == key) return window;
            for (auto *w : window->findChildren<QWidget *>(key)) {
                if (w->isVisible()) return w;
                if (!hidden) hidden = w;
            }
        }
        if (hidden) return hidden;
        throw Failure{"No widget named " + key};
    }
    const auto selector = target.toObject();
    if (selector.contains("path")) return resolve(selector.value("path"));
    QList<QWidget *> matches;
    for (auto *window : windows()) {
        QList<QWidget *> all{window};
        all += window->findChildren<QWidget *>();
        for (auto *w : all) {
            if (!w->isVisible()) continue;
            if (selector.contains("name") && w->objectName() != selector.value("name").toString()) continue;
            if (selector.contains("type") && !typeMatches(w, selector.value("type").toString())) continue;
            if (selector.contains("text") && textOf(w).remove('&') != selector.value("text").toString()) continue;
            matches << w;
        }
    }
    const int index = selector.value("index").toInt(0);
    if (index < 0 || index >= matches.size()) throw Failure{"No widget matches " + QString(QJsonDocument(selector).toJson(QJsonDocument::Compact))};
    return matches[index];
}

ConverterWindow *mainWindow() {
    for (auto *w : QApplication::topLevelWidgets())
        if (auto *c = qobject_cast<ConverterWindow *>(w); c && c->isVisible()) return c;
    for (auto *w : QApplication::topLevelWidgets()) if (auto *c = qobject_cast<ConverterWindow *>(w)) return c;
    throw Failure{"The main window is not open"};
}

QPoint pointIn(QWidget *w, const QJsonValue &value) {
    const auto a = value.toArray();
    if (a.size() == 2) return QPoint(a[0].toInt(), a[1].toInt());
    // Una casilla o un botón de opción solo responden sobre la marca y el texto: el centro de uno que ocupa todo el
    // ancho de su tarjeta cae fuera. Se pulsa sobre la marca.
    if (qobject_cast<QCheckBox *>(w) || qobject_cast<QRadioButton *>(w)) return QPoint(std::min(8, w->width() / 2), w->height() / 2);
    return w->rect().center();
}

void mouse(QWidget *w, QEvent::Type type, QPoint local, Qt::MouseButton button, Qt::MouseButtons buttons) {
    QMouseEvent event(type, local, w->window()->mapFromGlobal(w->mapToGlobal(local)), w->mapToGlobal(local), button, buttons, Qt::NoModifier);
    QApplication::sendEvent(w, &event);
}

void requireUsable(QWidget *w) {
    if (!w->isVisible()) throw Failure{pathOf(w) + " is not visible"};
    if (!w->isEnabled()) throw Failure{pathOf(w) + " is disabled"};
}

void key(QWidget *w, int code, const QString &text = {}, Qt::KeyboardModifiers modifiers = Qt::NoModifier) {
    QKeyEvent press(QEvent::KeyPress, code, modifiers, text), release(QEvent::KeyRelease, code, modifiers, text);
    QApplication::sendEvent(w, &press);
    QApplication::sendEvent(w, &release);
}

int keyCode(const QString &name) {
    static const QMap<QString, int> keys{{"Return", Qt::Key_Return}, {"Enter", Qt::Key_Enter}, {"Tab", Qt::Key_Tab},
        {"Escape", Qt::Key_Escape}, {"Backspace", Qt::Key_Backspace}, {"Delete", Qt::Key_Delete}, {"Up", Qt::Key_Up},
        {"Down", Qt::Key_Down}, {"Left", Qt::Key_Left}, {"Right", Qt::Key_Right}, {"Home", Qt::Key_Home},
        {"End", Qt::Key_End}, {"Space", Qt::Key_Space}, {"PageUp", Qt::Key_PageUp}, {"PageDown", Qt::Key_PageDown}};
    if (!keys.contains(name)) throw Failure{"Unknown key " + name};
    return keys.value(name);
}

QJsonObject image(const QPixmap &pixmap, const QString &path) {
    if (!path.isEmpty()) {
        if (!pixmap.save(path, "PNG")) throw Failure{"Could not save " + path};
        return {{"path", path}, {"size", QJsonArray{pixmap.width(), pixmap.height()}}};
    }
    QByteArray png; QBuffer buffer(&png); buffer.open(QIODevice::WriteOnly); pixmap.save(&buffer, "PNG");
    return {{"png", QString::fromLatin1(png.toBase64())}, {"size", QJsonArray{pixmap.width(), pixmap.height()}}};
}

// Condición de `wait`: la propiedad (o el campo de describe) del widget cumple equals / contains / not / exists.
bool satisfied(const QJsonObject &request, QJsonValue *current) {
    QWidget *w = nullptr;
    try { w = resolve(request.value("target")); } catch (const Failure &) { w = nullptr; }
    if (request.value("exists").isBool()) { *current = w != nullptr; return (w != nullptr) == request.value("exists").toBool(); }
    if (!w) return false;
    const auto field = request.value("property").toString("text");
    const auto described = describe(w);
    *current = described.contains(field) ? described.value(field) : jsonOf(w->property(field.toLatin1()));
    if (request.contains("equals")) return *current == request.value("equals");
    if (request.contains("not")) return *current != request.value("not");
    if (request.contains("contains")) return current->toString().contains(request.value("contains").toString());
    return true;
}

}  // namespace

AutomationServer::AutomationServer(const QString &name, Paths profilePaths, QObject *parent)
    : QObject(parent), server_(new QLocalServer(this)), paths_(std::move(profilePaths)) {
    server_->setSocketOptions(QLocalServer::UserAccessOption);
    token_ = QString::number(QRandomGenerator::system()->generate64(), 16) + QString::number(QRandomGenerator::system()->generate64(), 16);
    server_->setObjectName(name.isEmpty() ? QString("cdng2braw-%1").arg(QCoreApplication::applicationPid()) : name);
    connect(server_, &QLocalServer::newConnection, this, &AutomationServer::accept);
}

bool AutomationServer::listen(QString *error) {
    QLocalServer::removeServer(server_->objectName());
    if (server_->listen(server_->objectName())) return true;
    if (error) *error = server_->errorString();
    return false;
}

QString AutomationServer::serverName() const { return server_->fullServerName(); }

QString AutomationServer::writeConnectionFile(QString *error) const {
    const auto path = QDir::temp().filePath("cdng2braw-automation.json");
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate)) { if (error) *error = file.errorString(); return {}; }
    file.setPermissions(QFile::ReadOwner | QFile::WriteOwner);
    file.write(QJsonDocument(QJsonObject{{"socket", serverName()}, {"token", token_},
                                         {"pid", QCoreApplication::applicationPid()}}).toJson(QJsonDocument::Compact));
    return path;
}

void AutomationServer::accept() {
    while (auto *socket = server_->nextPendingConnection()) {
        connect(socket, &QLocalSocket::readyRead, this, [this, socket] { read(socket); });
        connect(socket, &QLocalSocket::disconnected, socket, &QObject::deleteLater);
    }
}

void AutomationServer::read(QLocalSocket *socket) {
    while (socket->canReadLine()) {
        const auto line = socket->readLine();
        QJsonParseError parse{};
        const auto request = QJsonDocument::fromJson(line, &parse).object();
        if (parse.error != QJsonParseError::NoError) { reply(socket, {{"ok", false}, {"error", "Invalid JSON"}}); continue; }
        if (request.value("token").toString() != token_) { reply(socket, {{"id", request.value("id")}, {"ok", false}, {"error", "Invalid token"}}); continue; }
        handle(socket, request);
    }
}

void AutomationServer::reply(QLocalSocket *socket, QJsonObject message) {
    if (!socket || socket->state() != QLocalSocket::ConnectedState) return;
    socket->write(QJsonDocument(message).toJson(QJsonDocument::Compact) + '\n');
    socket->flush();
}

void AutomationServer::subscribe(QLocalSocket *socket) {
    if (socket && !subscribers_.contains(socket)) subscribers_ << socket;
    auto broadcast = [this](QJsonObject event) {
        for (const auto &s : subscribers_) reply(s, event);
    };
    for (auto *window : QApplication::topLevelWidgets()) {
        for (auto *job : window->findChildren<ConversionJob *>()) {
            if (tapped_.contains(job)) continue;
            tapped_ << job;
            connect(job, &QObject::destroyed, this, [this, job] { tapped_.remove(job); });
            connect(job, &ConversionJob::message, this, [broadcast](const QString &text) { broadcast({{"event", "conversion_message"}, {"text", text}}); });
            connect(job, &ConversionJob::progress, this, [broadcast](int done, int total, double seconds) {
                broadcast({{"event", "conversion_progress"}, {"done", done}, {"total", total}, {"seconds", seconds}});
            });
            connect(job, &ConversionJob::finished, this, [broadcast](bool ok, bool cancelled, const QString &detail) {
                broadcast({{"event", "conversion_finished"}, {"success", ok}, {"cancelled", cancelled}, {"detail", detail}});
            });
        }
        for (auto *preview : window->findChildren<PreviewService *>()) {
            if (tapped_.contains(preview)) continue;
            tapped_ << preview;
            connect(preview, &QObject::destroyed, this, [this, preview] { tapped_.remove(preview); });
            connect(preview, &PreviewService::ready, this, [broadcast](const QJsonObject &meta, const QImage &image) {
                auto event = meta; event["event"] = "preview_ready"; event.remove("jpeg");
                event["image_size"] = QJsonArray{image.width(), image.height()}; broadcast(event);
            });
            connect(preview, &PreviewService::noiseReady, this, [broadcast](const QJsonObject &meta, const QImage &, const QImage &, const QImage &) {
                auto event = meta; event["event"] = "noise_ready";
                for (const auto *k : {"before", "after", "overview"}) event.remove(k);
                broadcast(event);
            });
            connect(preview, &PreviewService::error, this, [broadcast](const QString &text) { broadcast({{"event", "preview_error"}, {"message", text}}); });
        }
    }
}

void AutomationServer::handle(QLocalSocket *socket, const QJsonObject &request) {
    const auto id = request.value("id");
    const auto cmd = request.value("cmd").toString();
    QPointer<QLocalSocket> guard(socket);
    auto ok = [&](QJsonValue result = {}) { reply(guard, {{"id", id}, {"ok", true}, {"result", result}}); };
    try {
        if (!subscribers_.isEmpty()) subscribe(nullptr);   // engancha trabajos y vistas previas nuevos
        if (cmd == "hello") {
            ok(QJsonObject{{"app", QCoreApplication::applicationName()}, {"version", QCoreApplication::applicationVersion()},
                           {"pid", QCoreApplication::applicationPid()}, {"language", currentLanguage()}});
        } else if (cmd == "tree") {
            QJsonArray out;
            const bool all = request.value("all").toBool();
            if (request.contains("target")) collect(resolve(request.value("target")), all, out);
            else for (auto *w : windows()) collect(w, all, out);
            ok(out);
        } else if (cmd == "describe") {
            ok(describe(resolve(request.value("target"))));
        } else if (cmd == "dialogs") {
            QJsonArray out;
            for (auto *w : windows()) {
                auto o = describe(w);
                if (auto *box = qobject_cast<QMessageBox *>(w)) {
                    QJsonArray buttons;
                    for (auto *b : box->buttons()) buttons << QJsonObject{{"text", b->text().remove('&')}, {"path", pathOf(b)}};
                    o["informative"] = box->informativeText(); o["buttons"] = buttons;
                }
                out << o;
            }
            ok(out);
        } else if (cmd == "get") {
            auto *w = resolve(request.value("target"));
            const auto property = request.value("property").toString();
            const auto described = describe(w);
            if (described.contains(property)) ok(described.value(property));
            else {
                const auto value = w->property(property.toLatin1());
                if (!value.isValid()) throw Failure{"No property " + property + " on " + pathOf(w)};
                ok(jsonOf(value));
            }
        } else if (cmd == "set") {
            auto *w = resolve(request.value("target"));
            const auto property = request.value("property").toString();
            // Posiciones y tamaños (pos, size, geometry, minimumSize…) llegan como listas [x, y], [w, h] o [x, y, w, h].
            auto value = request.value("value").toVariant();
            const auto meta = w->metaObject()->property(w->metaObject()->indexOfProperty(property.toLatin1()));
            if (const auto list = request.value("value").toArray(); request.value("value").isArray() && meta.isValid()) {
                const auto at = [&](int i) { return list.at(i).toInt(); };
                if (meta.type() == QVariant::Point && list.size() == 2) value = QPoint(at(0), at(1));
                else if (meta.type() == QVariant::Size && list.size() == 2) value = QSize(at(0), at(1));
                else if (meta.type() == QVariant::Rect && list.size() == 4) value = QRect(at(0), at(1), at(2), at(3));
            }
            if (!w->setProperty(property.toLatin1(), value))
                throw Failure{"Could not set " + property + " on " + pathOf(w)};
            ok(describe(w));
        } else if (cmd == "click") {
            auto *w = resolve(request.value("target"));
            requireUsable(w);
            const auto at = pointIn(w, request.value("at"));
            // Primero la respuesta: si el clic abre una ventana modal, su bucle no debe retener la respuesta.
            ok();
            QTimer::singleShot(0, w, [target = QPointer<QWidget>(w), at] {
                if (target) mouse(target, QEvent::MouseButtonPress, at, Qt::LeftButton, Qt::LeftButton);
                if (target) mouse(target, QEvent::MouseButtonRelease, at, Qt::LeftButton, Qt::NoButton);
            });
        } else if (cmd == "drag") {
            auto *w = resolve(request.value("target"));
            requireUsable(w);
            const auto from = pointIn(w, request.value("from")), to = pointIn(w, request.value("to"));
            mouse(w, QEvent::MouseButtonPress, from, Qt::LeftButton, Qt::LeftButton);
            for (int step = 1; step <= 8; ++step)
                mouse(w, QEvent::MouseMove, from + (to - from) * step / 8, Qt::NoButton, Qt::LeftButton);
            mouse(w, QEvent::MouseButtonRelease, to, Qt::LeftButton, Qt::NoButton);
            ok();
        } else if (cmd == "type") {
            auto *w = resolve(request.value("target"));
            requireUsable(w);
            w->setFocus(Qt::OtherFocusReason);
            if (request.value("clear").toBool(true)) {
                key(w, Qt::Key_End);
                for (int i = textOf(w).size(); i > 0; --i) key(w, Qt::Key_Backspace);
            }
            for (const auto &c : request.value("text").toString()) key(w, 0, QString(c));
            if (request.value("enter").toBool()) key(w, Qt::Key_Return);
            ok(describe(w));
        } else if (cmd == "key") {
            auto *w = request.contains("target") ? resolve(request.value("target")) : QApplication::focusWidget();
            if (!w) throw Failure{"No focused widget"};
            const int code = keyCode(request.value("key").toString());
            ok();
            QTimer::singleShot(0, w, [target = QPointer<QWidget>(w), code] { if (target) key(target, code); });
        } else if (cmd == "select") {
            auto *combo = qobject_cast<QComboBox *>(resolve(request.value("target")));
            if (!combo) throw Failure{"select needs a combo box"};
            requireUsable(combo);
            int index = request.value("index").toInt(-1);
            if (request.contains("text")) index = combo->findText(request.value("text").toString());
            if (request.contains("data")) index = combo->findData(request.value("data").toVariant());
            if (index < 0 || index >= combo->count()) throw Failure{"No such item"};
            combo->setCurrentIndex(index);
            emit combo->activated(index);
            ok(describe(combo));
        } else if (cmd == "drop") {
            auto *w = request.contains("target") ? resolve(request.value("target")) : mainWindow();
            QMimeData mime;
            QList<QUrl> urls;
            for (const auto &p : request.value("paths").toArray()) urls << QUrl::fromLocalFile(p.toString());
            mime.setUrls(urls);
            const QPoint at = w->rect().center();
            QDragEnterEvent enter(at, Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
            QApplication::sendEvent(w, &enter);
            QDragMoveEvent move(at, Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
            QApplication::sendEvent(w, &move);
            QDropEvent drop(at, Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
            QApplication::sendEvent(w, &drop);
            ok(QJsonObject{{"accepted", drop.isAccepted()}});
        } else if (cmd == "invoke") {
            QObject *object = request.contains("target") ? static_cast<QObject *>(resolve(request.value("target"))) : mainWindow();
            const auto method = request.value("method").toString().toLatin1();
            // Asíncrono: una acción que abre un diálogo modal no bloquea la respuesta.
            if (!QMetaObject::invokeMethod(object, method.constData(), Qt::QueuedConnection))
                throw Failure{"No invokable method " + QString(method)};
            ok();
        } else if (cmd == "wait") {
            const int timeout = request.value("timeout").toInt(10000);
            auto *timer = new QTimer(this);
            auto started = std::make_shared<qint64>(QDateTime::currentMSecsSinceEpoch());
            auto check = [this, guard, id, request, timeout, timer, started] {
                QJsonValue current;
                if (satisfied(request, &current)) {
                    reply(guard, {{"id", id}, {"ok", true}, {"result", current}}); timer->deleteLater(); return;
                }
                if (QDateTime::currentMSecsSinceEpoch() - *started > timeout) {
                    reply(guard, {{"id", id}, {"ok", false}, {"error", "Timeout"}, {"current", current}}); timer->deleteLater();
                }
            };
            connect(timer, &QTimer::timeout, this, check);
            timer->start(50);
            QTimer::singleShot(0, this, check);
        } else if (cmd == "screenshot") {
            QWidget *w = request.contains("target") ? resolve(request.value("target")) : windows().value(0);
            if (!w) throw Failure{"No window"};
            ok(image(w->grab(), request.value("path").toString()));
        } else if (cmd == "state") {
            auto state = mainWindow()->snapshot();
            state.remove("geometry");
            ok(QJsonObject::fromVariantMap(state));
        } else if (cmd == "profile") {
            // El perfil va en «profile»: «id» es el número de la petición en el protocolo.
            const auto spec = ProfileSpec::byId(request.value("profile").toString());
            if (spec.id.isEmpty()) throw Failure{"Unknown profile"};
            ProfileStore store(paths_, Bundle::embedded(spec));
            const auto action = request.value("action").toString("status");
            QJsonObject result;
            if (action == "install" || action == "remove") {
                const auto operation = action == "install" ? store.install() : store.remove();
                result = {{"ok", operation.ok}, {"changed", operation.changed}, {"message", operation.message}};
            }
            result["state"] = stateName(store.inspect().state);
            QMetaObject::invokeMethod(mainWindow(), "refreshProfile", Qt::QueuedConnection);
            ok(result);
        } else if (cmd == "subscribe") {
            subscribe(socket);
            ok();
        } else if (cmd == "quit") {
            ok();
            QTimer::singleShot(0, qApp, [] { QApplication::exit(0); });
        } else {
            throw Failure{"Unknown command " + cmd};
        }
    } catch (const Failure &failure) {
        reply(guard, {{"id", id}, {"ok", false}, {"error", failure.message}});
    }
}

}  // namespace cdng
