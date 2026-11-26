#include "i18n.h"
#include "conversion_job.h"
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonArray>
#include <QJsonObject>
#include <QProcessEnvironment>

namespace cdng {
ConversionJob::ConversionJob(QString backend, QObject *parent) : QObject(parent), backend_(std::move(backend)) {
    if (backend_.isEmpty()) {
#ifdef Q_OS_MAC
        backend_ = QDir(QCoreApplication::applicationDirPath()).filePath("../Resources/converter/cdng-convert");
#elif defined(Q_OS_WIN)
        backend_ = QDir(QCoreApplication::applicationDirPath()).filePath("converter/cdng-convert.exe");
#else
        backend_ = QDir(QCoreApplication::applicationDirPath()).filePath("converter/cdng-convert");
#endif
    }
    connect(&process_, &QProcess::readyReadStandardOutput, this, &ConversionJob::consume);
    connect(&process_, &QProcess::readyReadStandardError, this, [this] {
        diagnostics_ += QString::fromUtf8(process_.readAllStandardError());
        diagnostics_ = diagnostics_.right(16000);
    });
    connect(&process_, qOverload<int, QProcess::ExitStatus>(&QProcess::finished), this, &ConversionJob::finish);
    connect(&process_, &QProcess::errorOccurred, this, [this](QProcess::ProcessError error) {
        if (error != QProcess::FailedToStart) return;
        busy_ = false; workspace_.reset();
        emit finished(false, false, t("Could not start the engine: ") + process_.errorString());
    });
}
bool ConversionJob::available() const { return QFileInfo(backend_).isExecutable(); }
bool ConversionJob::start(const ConversionOptions &options, QString *error) {
    auto fail = [&](const QString &text) { if (error) *error = text; return false; };
    if (busy_) return fail(t("A conversion is already running."));
    if (!available()) return fail(t("The app's conversion engine is missing. Use the complete package."));
    if (!QFileInfo(options.input).isDir()) return fail(t("Select the CinemaDNG sequence folder."));
    const QFileInfo output(options.output);
    if (output.suffix().compare("braw", Qt::CaseInsensitive) != 0 || !output.dir().exists())
        return fail(t("Choose a .braw file in an existing folder."));
    if (output.exists() || output.isSymLink()) return fail(t("The destination already exists. Choose another name."));
    workspace_ = std::make_unique<QTemporaryDir>(QDir::tempPath() + "/cdng-job-XXXXXX");
    if (!workspace_->isValid()) return fail(t("Could not create the temporary directory."));
    QFile request(workspace_->filePath("request.json"));
    const auto data = QJsonDocument(QJsonObject{{"input", QFileInfo(options.input).absoluteFilePath()},
        {"output", output.absoluteFilePath()}, {"q", options.q}, {"workers", options.workers},
        {"audio", options.audio}, {"gyro_gcsv", options.gyroGcsv}, {"gyro_fsg2", options.gyroFsg2}, {"gyro_braw", options.gyroBraw}, {"gyro_project", options.gyroProject}, {"camera_type", options.cameraType}, {"cancel", workspace_->filePath("cancel")}, {"control", workspace_->filePath("control.json")},
        {"compression", options.compression}, {"ratio", options.ratio},
        {"nr_luma", options.nrLuma}, {"nr_chroma", options.nrChroma}, {"noise_synthesis", options.noiseSynthesis},
        {"match_cinemadng_rendering", options.matchCinemaDngRendering},
        {"width", qMax(0, options.size.width())},
        {"height", qMax(0, options.size.height())}}).toJson();
    if (!request.open(QIODevice::WriteOnly) || request.write(data) != data.size()) {
        workspace_.reset(); return fail(t("Could not prepare the conversion."));
    }
    request.close();
    if (!writeControl(options.workers)) { workspace_.reset(); return fail(t("Could not prepare the conversion.")); }
    pending_.clear(); diagnostics_.clear(); outcome_.clear(); failure_.clear(); summary_.clear(); replaced_.clear();
    output_ = output.absoluteFilePath(); busy_ = true; cancelling_ = false;
    auto env = QProcessEnvironment::systemEnvironment();
    env.insert("CDNG_LANGUAGE", currentLanguage());
    for (const auto &key : {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "DYLD_INSERT_LIBRARIES", "CDNG_NATIVE_LIBRARY", "CDNG_ENCODER"})
        env.remove(key);
    process_.setProcessEnvironment(env);
    process_.setWorkingDirectory(workspace_->path());
    process_.start(backend_, {"--request", request.fileName()});
    return true;
}
static QString compressionSummary(const QJsonObject &event) {
    // The rate control never clamps the codec: it can miss the requested ratio in either direction (D10).
    const double achieved = event.value("ratio_achieved").toDouble();
    if (achieved <= 0) return {};
    const int qMin = event.value("q_min").toInt(), qMax = event.value("q_max").toInt();
    const auto range = qMin == qMax ? QString::number(qMin) : QString("%1–%2").arg(qMin).arg(qMax);
    auto text = QString(t("Compression achieved: %1:1 · quantiser q %2")).arg(achieved, 0, 'f', 1).arg(range);
    const auto requested = event.value("ratio_requested");
    if (requested.isDouble()) {
        const double wanted = requested.toDouble();
        text = QString(t("Compression achieved: %1:1 (requested %2:1) · quantiser q %3")).arg(achieved, 0, 'f', 1).arg(wanted, 0, 'g', 3).arg(range);
        if (achieved < wanted * .9)
            text += "\n" + QString(t("The clip needs more data than the requested ratio: some frames are already at the coarsest quantiser (q %1), so the file is larger than requested.")).arg(event.value("q_ceiling").toInt());
        else if (achieved > wanted * 1.1)
            text += "\n" + t("The codec limits prevent a finer quantiser on some frames (very dark areas or point lights), so the file is smaller than requested. Quality is the best this encoder can reach.");
    }
    return text;
}
void ConversionJob::consume() {
    pending_ += process_.readAllStandardOutput();
    // A damaged worker must not grow the UI's memory without bound.
    if (pending_.size() > 1024 * 1024) pending_ = pending_.right(32768);
    int newline;
    while ((newline = pending_.indexOf('\n')) >= 0) {
        const auto line = pending_.left(newline); pending_.remove(0, newline + 1);
        const auto event = QJsonDocument::fromJson(line).object();
        const auto kind = event.value("event").toString();
        if (kind == "error") { outcome_ = kind; failure_ = event.value("message").toString(); }
        else if (kind == "completed") {
            outcome_ = kind; summary_ = compressionSummary(event); replaced_.clear();
            const auto gyro = event.value("gyro").toObject();
            QStringList files;
            if (gyro.value("braw").toBool()) files << t("inside the BRAW");
            for (const char *key : {"gyroflow", "gcsv", "fsg2"}) if (gyro.contains(key)) files << QFileInfo(gyro.value(key).toString()).fileName();
            if (!files.isEmpty()) summary_ += "\n" + QString(t("Gyro (Gyro2): %1")).arg(files.join(", "));
            if (gyro.contains("lens")) summary_ += "\n" + QString(t("No lens profile for Gyroflow: %1")).arg(gyro.value("lens").toString());
            if (gyro.contains("note")) summary_ += " · " + gyro.value("note").toString();
            if (gyro.contains("error")) summary_ += "\n" + QString(t("The gyro could not be written: %1")).arg(gyro.value("error").toString());
            for (const auto &n : event.value("replaced_frames").toArray()) replaced_ << n.toInt();
            if (!replaced_.isEmpty()) {
                QStringList numbers; for (int n : replaced_) numbers << QString::number(n);
                summary_ += "\n" + QString(t("%1 damaged or missing frames replaced with black: %2")).arg(replaced_.size()).arg(numbers.join(", "));
            }
        }
        else if (kind == "cancelled") outcome_ = kind;
        else if (kind == "progress") {
            emit progress(event.value("done").toInt(), event.value("total").toInt(), event.value("seconds").toDouble());
        } else if (kind == "status") emit message(event.value("message").toString());
        else if (kind == "validation")
            emit message(QString(t("Checking frames: %1 / %2")).arg(event.value("done").toInt()).arg(event.value("total").toInt()));
        else if (kind == "started") emit message(t("Converting CinemaDNG → Blackmagic RAW…"));
    }
}
void ConversionJob::finish(int code, QProcess::ExitStatus status) {
    consume(); diagnostics_ += QString::fromUtf8(process_.readAllStandardError());
    const bool success = code == 0 && status == QProcess::NormalExit && outcome_ == "completed" && QFileInfo(output_).size() > 0;
    const bool cancelled = code == 3 && status == QProcess::NormalExit && outcome_ == "cancelled";
    auto detail = success ? output_ + (summary_.isEmpty() ? QString() : "\n\n" + summary_) : cancelled ? QString(t("Conversion cancelled. No incomplete BRAW was published.")) : failure_;
    if (!success && !cancelled) {
        if (detail.isEmpty()) detail = t("The engine stopped without completing the conversion.");
        if (!diagnostics_.isEmpty()) detail += "\n\n" + diagnostics_.right(12000);
    }
    busy_ = false; cancelling_ = false; workspace_.reset();
    emit finished(success, cancelled, detail);
}
bool ConversionJob::writeControl(int workers) {
    // Se escribe aparte y se renombra: el motor nunca lee un fichero a medias.
    QFile file(workspace_->filePath("control.json.new"));
    const auto data = QJsonDocument(QJsonObject{{"workers", workers}}).toJson(QJsonDocument::Compact);
    if (!file.open(QIODevice::WriteOnly) || file.write(data) != data.size()) return false;
    file.close();
    QFile::remove(workspace_->filePath("control.json"));
    return QFile::rename(file.fileName(), workspace_->filePath("control.json"));
}
void ConversionJob::setWorkers(int workers) {
    if (busy_ && workspace_) writeControl(workers);
}
void ConversionJob::cancel() {
    if (!busy_ || cancelling_ || !workspace_) return;
    QFile file(workspace_->filePath("cancel"));
    if (!file.open(QIODevice::WriteOnly)) {
        emit message(t("Could not request cancellation. The conversion is continuing.")); return;
    }
    file.close(); cancelling_ = true;
    emit message(t("Cancelling and shutting down the conversion workers…"));
}
}
