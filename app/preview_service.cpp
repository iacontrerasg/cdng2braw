#include "i18n.h"
#include "preview_service.h"
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonArray>
#include <QPainter>
#include <QPainterPath>
#include <QProcessEnvironment>

namespace cdng {
PreviewService::PreviewService(QString backend, QObject *parent) : QObject(parent), backend_(std::move(backend)) {
    deadline_.setSingleShot(true); deadline_.setInterval(60000);
    connect(&deadline_, &QTimer::timeout, this, [this] {
        stop(); emit error(t("The preview timed out. Select the frame again."));
    });
    connect(&process_, &QProcess::started, this, &PreviewService::send);
    connect(&process_, &QProcess::readyReadStandardOutput, this, &PreviewService::read);
    connect(&process_, &QProcess::readyReadStandardError, this, [this] { process_.readAllStandardError(); });
    connect(&process_, &QProcess::errorOccurred, this, [this](QProcess::ProcessError kind) {
        if (kind != QProcess::FailedToStart) return;
        deadline_.stop(); inflight_ = false; pending_ = false;
        emit error(t("Could not start the preview: ") + process_.errorString());
    });
    connect(&process_, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, [this](int code, QProcess::ExitStatus status) {
        const bool unexpected = inflight_ || pending_;
        deadline_.stop(); inflight_ = false; pending_ = false; buffer_.clear();
        if (unexpected && (code != 0 || status != QProcess::NormalExit)) emit error(t("The preview process stopped. You can try again."));
    });
}
PreviewService::~PreviewService() { stop(); }
void PreviewService::request(const QString &folder, int frame, QSize size) {
    enqueue({{"input", folder}, {"frame", frame}, {"width", qMax(0, size.width())}, {"height", qMax(0, size.height())}});
}
void PreviewService::requestScan(const QString &folder) {
    enqueue({{"action", "scan"}, {"input", folder}});
}
void PreviewService::requestNoise(const QString &folder, int frame, QSize size, QRect crop, const QList<double> &noise, double exposure,
                                  const QString &compression, int ratio, int q) {
    enqueue({{"action", "noise"}, {"input", folder}, {"frame", frame}, {"width", size.width()}, {"height", size.height()},
        {"roi", QJsonArray{crop.x(), crop.y(), crop.width(), crop.height()}},
        {"nr_luma", noise[0]}, {"nr_chroma", noise[1]}, {"exposure", exposure},
        {"compression", compression}, {"ratio", ratio}, {"q", q}});
}
void PreviewService::enqueue(QJsonObject request) {
    if (!QFileInfo(backend_).isExecutable()) { emit error(t("The preview requires the engine included in the complete package.")); return; }
    request.insert("id", ++generation_); wanted_ = std::move(request); pending_ = true;
    if (process_.state() == QProcess::NotRunning) {
        auto env = QProcessEnvironment::systemEnvironment();
        env.insert("CDNG_LANGUAGE", currentLanguage());
        for (const auto &name : {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "DYLD_INSERT_LIBRARIES", "CDNG_NATIVE_LIBRARY", "CDNG_ENCODER"}) env.remove(name);
        process_.setProcessEnvironment(env);
        process_.start(backend_, {"--preview-server"});
    } else send();
}
void PreviewService::send() {
    if (!pending_ || inflight_ || process_.state() != QProcess::Running) return;
    const auto bytes = QJsonDocument(wanted_).toJson(QJsonDocument::Compact) + '\n';
    if (process_.write(bytes) != bytes.size()) { stop(); emit error(t("Could not request the frame.")); return; }
    pending_ = false; inflight_ = true; deadline_.start();
}
void PreviewService::read() {
    buffer_ += process_.readAllStandardOutput();
    if (buffer_.size() > 8 * 1024 * 1024) { stop(); emit error(t("The preview response is too large.")); return; }
    int newline;
    while ((newline = buffer_.indexOf('\n')) >= 0) {
        const auto line = buffer_.left(newline); buffer_.remove(0, newline + 1);
        const auto object = QJsonDocument::fromJson(line).object();
        const auto kind = object.value("event").toString();
        if (kind == "error") {
            deadline_.stop(); inflight_ = false; pending_ = false;
            emit error(object.value("message").toString(t("Could not start the preview.")));
            continue;
        }
        if (kind == "scan_clip") {   // avance de la exploración: no cierra la petición
            if (object.value("id").toInt(-1) == generation_) emit scanProgress(object.value("clip").toObject(), object.value("index").toInt(), object.value("total").toInt());
            continue;
        }
        if (kind != "preview" && kind != "preview_error" && kind != "noise" && kind != "scan") continue;
        inflight_ = false; deadline_.stop();
        if (object.value("id").toInt(-1) == generation_) {
            if (kind == "scan") emit scanReady(object);
            else if (kind == "noise") {
                auto decode = [&](const char *key) { return QImage::fromData(QByteArray::fromBase64(object.value(key).toString().toLatin1())); };
                const auto before = decode("before"), after = decode("after"), overview = decode("overview");
                if (before.isNull() || after.isNull() || overview.isNull()) emit error(t("Could not read the preview image."));
                else emit noiseReady(object, before, after, overview);
            } else if (object.value("event") == "preview") {
                // The field keeps its historical name: the Python worker sends JPEG, the C++ engine PPM.
                const auto image = QImage::fromData(QByteArray::fromBase64(object.value("jpeg").toString().toLatin1()));
                if (image.isNull()) emit error(t("Could not read the preview image."));
                else emit ready(object, image);
            } else emit error(object.value("message").toString(t("Could not develop this DNG for the preview.")));
        }
        send();
    }
}
void PreviewService::stop() {
    ++generation_; pending_ = false; inflight_ = false; deadline_.stop(); buffer_.clear();
    if (process_.state() != QProcess::NotRunning) { process_.kill(); process_.waitForFinished(1000); }
}
PreviewCanvas::PreviewCanvas(QWidget *parent) : QWidget(parent) {
    setMinimumSize(300, 160); setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    caption_ = t("Select a CinemaDNG sequence");
}
void PreviewCanvas::setImage(QImage image, QString caption) { image_ = std::move(image); caption_ = std::move(caption); update(); }
void PreviewCanvas::clear(const QString &message) { image_ = {}; caption_ = message; update(); }
void PreviewCanvas::paintEvent(QPaintEvent *) {
    QPainter painter(this); painter.setRenderHint(QPainter::Antialiasing);
    QPainterPath shape; shape.addRoundedRect(QRectF(rect()), 6, 6); painter.setClipPath(shape);
    painter.fillRect(rect(), QColor("#111316"));
    if (image_.isNull()) {
        painter.setPen(QColor("#a7acb7")); painter.drawText(rect().adjusted(20, 20, -20, -20), Qt::AlignCenter | Qt::TextWordWrap, caption_); return;
    }
    const auto size = image_.size().scaled(this->size(), Qt::KeepAspectRatio);
    const QRect target((width() - size.width()) / 2, (height() - size.height()) / 2, size.width(), size.height());
    painter.setRenderHint(QPainter::SmoothPixmapTransform); painter.drawImage(target, image_);
    const auto badge = QRect(10, height() - 32, qMin(width() - 20, painter.fontMetrics().horizontalAdvance(caption_) + 18), 23);
    painter.setBrush(QColor(0, 0, 0, 175)); painter.setPen(Qt::NoPen); painter.drawRoundedRect(badge, 4, 4);
    painter.setPen(Qt::white); painter.drawText(badge.adjusted(8, 0, -8, 0), Qt::AlignVCenter | Qt::AlignLeft, caption_);
}
}
