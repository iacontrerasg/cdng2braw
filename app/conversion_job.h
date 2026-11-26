#pragma once
#include <QObject>
#include <QProcess>
#include <QTemporaryDir>
#include <cstdlib>
#include <memory>
#include <QSize>

namespace cdng {
// Calidad constante: los niveles de las cámaras de Blackmagic y la q del códec de cada uno (los que escribe la Pocket 4K
// en la cabecera: Q0/Q1/Q3/Q5 = q 0/2/9/20). El motor admite cualquier q de 0 a 24; la app ofrece estos.
struct QualityLevel { const char *name; int q; };
inline const QList<QualityLevel> &qualityLevels() {
    static const QList<QualityLevel> levels{{"Q0", 0}, {"Q1", 2}, {"Q3", 9}, {"Q5", 20}};
    return levels;
}
// El nivel más cercano a una q (ajustes guardados con la q libre de versiones anteriores).
inline QualityLevel nearestQualityLevel(int q) {
    QualityLevel best = qualityLevels().front();
    for (const auto &level : qualityLevels()) if (std::abs(level.q - q) < std::abs(best.q - q)) best = level;
    return best;
}
// Si la q coincide con un nivel, su número (0 para Q0); si no, -1 (es una q libre del modo avanzado).
inline int qualityLevelIndex(int q) {
    for (int i = 0; i < qualityLevels().size(); ++i) if (qualityLevels()[i].q == q) return i;
    return -1;
}
// Etiqueta de una q para el nombre del BRAW: el nivel si lo es («Q3»), o la q libre («q7»).
inline QString qualityTag(int q) {
    const int i = qualityLevelIndex(q);
    return i >= 0 ? QString(qualityLevels()[i].name) : QString("q%1").arg(q);
}
struct ConversionOptions {
    QString input;
    QString output;
    int q = 4;
    int workers = 2;
    bool audio = false;
    bool gyroBraw = true, gyroProject = true;   // giroscopio Gyro2 dentro del BRAW y proyecto con el objetivo
    bool gyroGcsv = false, gyroFsg2 = false;    // y en ficheros aparte, si los DNG lo traen
    QString cameraType = "auto";            // identidad del BRAW en las tomas de la fp: siempre «auto», la cámara del DNG
    QString compression = "quality";
    double ratio = 5.;
    double nrLuma = 0.;
    double nrChroma = 0.;
    bool noiseSynthesis = true;   // el descodificador repone el ruido que quita la compresión
    bool matchCinemaDngRendering = false;   // OG: viewing DWG/Intermediate (LUT Gen 1); default P3 Linear
    QSize size;
};
class ConversionJob : public QObject {
    Q_OBJECT
    QString backend_;
    QProcess process_;
    std::unique_ptr<QTemporaryDir> workspace_;
    QByteArray pending_;
    QString diagnostics_;
    QString output_;
    QString outcome_;
    QString failure_;
    QString summary_;   // relación de compresión conseguida, al terminar
    QList<int> replaced_;   // frames sustituidos por negro (dañados o ausentes), desde 1
    bool busy_ = false;
    bool cancelling_ = false;
    void consume();
    bool writeControl(int workers);
    void finish(int code, QProcess::ExitStatus status);
public:
    explicit ConversionJob(QString backend = {}, QObject *parent = nullptr);
    bool busy() const { return busy_; }
    bool available() const;
    QString backendPath() const { return backend_; }
    bool start(const ConversionOptions &options, QString *error);
    void cancel();
    // Procesos en paralelo de la conversión en curso: el motor relee el fichero de control mientras trabaja.
    void setWorkers(int workers);
    QList<int> replacedFrames() const { return replaced_; }
signals:
    void message(const QString &text);
    void progress(int done, int total, double seconds);
    void finished(bool success, bool cancelled, const QString &detail);
};
}
