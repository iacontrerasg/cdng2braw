#pragma once
#include "preview_service.h"
#include <QDialog>
#include <QSlider>
class QButtonGroup;
class QScrollArea;
class QDoubleSpinBox;
class QLabel;
class QComboBox;
class QSpinBox;
namespace cdng {
// Apagada, Suave, Media, Fuerte: luma / croma % del filtro de Wiener.
// El índice 4 es Personalizada.
constexpr int NoisePresets[4][2] = {{0, 0}, {20, 40}, {35, 60}, {50, 80}};
constexpr int CustomNoise = 4;
// Una resolución de salida que ofrece el comparador: clave de la ventana ("original", "3840x2160", "custom"…).
struct ResolutionOption {
    QString key, label;
    QSize size;
};
// Ajustes que el comparador recibe de la ventana y devuelve al aplicar: reducción de ruido y compresión.
struct ComparisonSettings {
    double luma = 0, chroma = 0;
    int preset = CustomNoise;
    QString compression = "ratio";   // "none" (solo en el comparador), "ratio" o "quality"
    int ratio = 5, q = 4;
    QList<ResolutionOption> resolutions;   // la primera, la nativa; la marcada, `resolution`
    QString resolution = "original";
};
class CropNavigator : public QWidget {
    Q_OBJECT
    QImage image_;
    QSize full_;
    QRect crop_;
    QRect imageRect() const;
    void moveTo(QPoint position);
public:
    explicit CropNavigator(QSize full, QImage image = {}, QWidget *parent = nullptr);
    QRect crop() const { return crop_; }
    void setImage(QImage image);
    void setCropSize(QSize size);
signals:
    void cropChanged();
protected:
    void paintEvent(QPaintEvent *) override;
    void mousePressEvent(QMouseEvent *) override;
    void mouseMoveEvent(QMouseEvent *) override;
};
class NoiseDialog : public QDialog {
    Q_OBJECT
    PreviewService *service_;
    CropNavigator *navigator_;
    QSpinBox *luma_, *chroma_;
    QComboBox *quality_;   // Q0/Q1/Q3/Q5, con su q como dato
    QComboBox *compression_, *ratio_, *resolution_;
    QLabel *beforeTitle_;
    QSize native_;
    QButtonGroup *presets_;
    QLabel *afterTitle_;
    QDoubleSpinBox *exposure_;
    QComboBox *cropSize_, *zoom_;
    QSlider *frameSlider_;
    QLabel *frameNumber_;
    QLabel *before_, *after_, *status_;
    QImage beforeImage_, afterImage_;
    void refreshPanes();
    // Los dos paneles enseñan la misma zona de la escena: se alinean por el centro, en proporción a cada imagen.
    QList<QScrollArea *> views_;
    QPointF centre_{.5, .5};
    bool syncing_ = false;
    void followView(int from);
    void applyCentre();
    QTimer timer_;
    QString folder_;
    int frame_;
    QSize size_;
    void request();
public:
    NoiseDialog(QString backend, QString folder, int frame, int total, QSize size, QImage overview,
                ComparisonSettings settings, QWidget *parent = nullptr);
    ComparisonSettings settings() const;
    QList<double> noise() const;
};
}
