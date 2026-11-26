#pragma once
#include "conversion_job.h"
#include "profile_window.h"
#include "preview_service.h"
#include <QElapsedTimer>
#include <QPointF>
#include <QJsonArray>
#include <QJsonObject>
#include <QMap>
#include <QWidget>
#include <QVariantMap>

class QLineEdit;
class QComboBox;
class QSpinBox;
class QCheckBox;
class QLabel;
class QMimeData;
class QPushButton;
class QProgressBar;
class QPlainTextEdit;
class QRadioButton;
class QButtonGroup;
class QSlider;
class QTimer;
class QTreeWidget;
namespace cdng {
class ConverterWindow : public QWidget {
    Q_OBJECT
    ProfileStore store_;
    ProfileWindow *profiles_;
    QMap<QString, ProfileWindow *> cameraProfiles_;   // perfil Camera Support que pide la cámara de la fuente
    QString requiredProfile_;                          // id del perfil que exige la secuencia cargada (vacío: ninguno)
    QString offeredFor_;                               // carpeta para la que ya se ofreció instalarlo
    ProfileWindow *profileWindow(const QString &id);
    bool profileReady();
    bool anyProfileOperation() const;
    ConversionJob *job_;
    QLineEdit *input_;       // carpeta del lote (una toma o una tarjeta con muchas)
    QLineEdit *output_;      // carpeta de destino: un BRAW por toma
    QButtonGroup *qualities_;   // niveles Q0/Q1/Q3/Q5; el id de cada botón es su q
    QWidget *qualityButtons_;    // los botones de los niveles; ceden su sitio al desplegable con la q libre
    QCheckBox *qCustomCheck_;    // q libre (0–24) como opción avanzada, solo en calidad fija
    QComboBox *qCustom_;         // q0…q24; los que son un nivel llevan su nombre («q9 (Q3)»); el dato es la q
    QCheckBox *gyroBraw_, *gyroFiles_, *gyroProject_;
    QCheckBox *noiseSynthesis_;   // ruido que repone el descodificador (campos de la cabecera de cada frame)
    QCheckBox *matchCinemaDng_;   // OG: viewing DWG/Intermediate; default desactivado = P3 Linear
    QWidget *matchCinemaDngRow_;
    QRadioButton *ratioMode_;
    QRadioButton *qualityMode_;
    QButtonGroup *ratios_;
    QButtonGroup *nrPresets_;
    QSpinBox *nrLuma_;      // reducción de ruido intra-frame, porcentaje 0–100
    QSpinBox *nrChroma_;
    QComboBox *language_;
    QComboBox *resolution_;
    QSpinBox *outputWidth_;
    QSpinBox *outputHeight_;
    QWidget *customSize_;
    QLabel *resolutionSize_;
    QPushButton *compareNoise_;
    QWidget *compressionCard_;
    QWidget *nrCard_;
    QWidget *advancedCard_;
    QSpinBox *workers_;
    QLabel *state_;
    QLabel *resolveHint_;   // al terminar un lote con tomas de la Pocket original: cómo abrirlas en Resolve
    QLabel *profileState_;
    QWidget *profileRow_;    // estado del perfil de cámara, solo si la toma lo necesita
    QProgressBar *progress_;
    QPlainTextEdit *details_;
    QPushButton *convert_;
    QPushButton *cancel_;
    QPushButton *profileButton_;
    QPushButton *chooseInput_;
    QPushButton *chooseOutput_;
    QPushButton *reveal_;
    QPushButton *selectAll_, *selectNone_;   // marcar o desmarcar todas las tomas del lote
    PreviewService *previewService_;
    PreviewCanvas *preview_;
    QSlider *frame_;
    QLabel *frameNumber_;
    QLabel *previewState_;
    QLabel *estimate_;
    QLabel *clipDetails_;          // detalles de la toma seleccionada, bajo la vista previa
    QLabel *sourceCompatibility_;  // resumen del lote
    QTreeWidget *clips_;           // tomas encontradas en la carpeta
    QString clipFolder_;           // toma seleccionada (la que muestran la vista previa y el comparador)
    QJsonArray scanned_;           // datos de cada toma (respuesta de «scan» del motor)
    // Cola de conversión del lote.
    QList<int> queue_;
    int current_ = -1, converted_ = 0, skipped_ = 0, failed_ = 0, batchSize_ = 0, convertedOg_ = 0;
    bool batchCancelled_ = false;
    bool batching_ = false;        // lote en curso: no se reexplora la carpeta
    QString loadedRoot_;           // carpeta explorada (el campo solo reexplora si cambia)
    QStringList batchLog_;
    QElapsedTimer batchTimer_;
    // Tiempo estimado: el trabajo se mide en frames × píxeles de la toma, para que una toma UHD pese más que una 1080.
    double batchWork_ = 0, doneWork_ = 0;
    QList<QPointF> workSamples_;   // (segundos, trabajo hecho) del último minuto: ritmo reciente
    double clipWork(int index) const;
    void showBatchProgress(double fraction);
    QTimer *previewTimer_;
    QJsonObject sourceInfo_;       // vista previa de la toma seleccionada
    Q_INVOKABLE void loadSource();
    void updateCompression();
    int selectedQuality() const;   // q efectiva: la del nivel marcado o la del spinbox si está la q libre
    void updateNoiseControls();
    void updateMatchCinemaDng();
    bool scannedHasOg() const;
    QString outputPathFor(const QJsonObject &clip) const;
    void showClips(bool final = true);
    void selectClip(const QString &folder);
    void startNext();
    void finishBatch();
    void updateBatchControls();
    QList<int> checkedClips() const;
    void setAllChecked(bool checked);
    void setBusy(bool busy);
    Q_INVOKABLE void start();
    Q_INVOKABLE void refreshProfile();
    void updateResolution(bool requestPreview = true);
    QSize selectedSize() const;
    QSize sizeForClip(const QJsonObject &clip) const;
    QSize sizeForKey(const QString &key, QSize source) const;
    Q_INVOKABLE void openNoiseDialog();
    Q_INVOKABLE void showAbout();
public:
    explicit ConverterWindow(ProfileStore store = ProfileStore(), QString backend = {}, QWidget *parent = nullptr);
    void offerInstall();
    QVariantMap snapshot() const;
    void restore(const QVariantMap &state);
    static QString droppedFolder(const QMimeData *mime);
signals:
    void languageChangeRequested(const QString &preference);
protected:
    void closeEvent(QCloseEvent *event) override;
    void showEvent(QShowEvent *event) override;
    void dragEnterEvent(QDragEnterEvent *event) override;
    void dragMoveEvent(QDragMoveEvent *event) override;
    void dropEvent(QDropEvent *event) override;
};
}
