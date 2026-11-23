#pragma once
#include <QImage>
#include <QJsonObject>
#include <QObject>
#include <QProcess>
#include <QTimer>
#include <QWidget>
#include <QRect>

namespace cdng {
class PreviewService : public QObject {
    Q_OBJECT
    QString backend_;
    QProcess process_;
    QTimer deadline_;
    QByteArray buffer_;
    QJsonObject wanted_;
    int generation_ = 0;
    bool pending_ = false;
    bool inflight_ = false;
    void send();
    void read();
    void enqueue(QJsonObject request);
public:
    explicit PreviewService(QString backend, QObject *parent = nullptr);
    ~PreviewService() override;
    void request(const QString &folder, int frame, QSize size = {});
    void requestScan(const QString &folder);   // tomas de una carpeta (lote), sin revelar
    void requestNoise(const QString &folder, int frame, QSize size, QRect crop, const QList<double> &noise, double exposure = 0,
                      const QString &compression = "none", int ratio = 5, int q = 4);
    void stop();
signals:
    void ready(const QJsonObject &metadata, const QImage &image);
    void noiseReady(const QJsonObject &metadata, const QImage &before, const QImage &after, const QImage &overview);
    void scanReady(const QJsonObject &result);
    void scanProgress(const QJsonObject &clip, int index, int total);   // cada toma según se explora
    void error(const QString &message);
};

class PreviewCanvas : public QWidget {
    QImage image_;
    QString caption_;
public:
    explicit PreviewCanvas(QWidget *parent = nullptr);
    void setImage(QImage image, QString caption);
    void clear(const QString &message);
    QImage image() const { return image_; }
protected:
    void paintEvent(QPaintEvent *event) override;
};
}
