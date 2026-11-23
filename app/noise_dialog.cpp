#include "noise_dialog.h"
#include "i18n.h"
#include "conversion_job.h"
#include <cmath>
#include <QButtonGroup>
#include <QComboBox>
#include <QDialogButtonBox>
#include <QDoubleSpinBox>
#include <QGridLayout>
#include <QJsonArray>
#include <QLabel>
#include <QMouseEvent>
#include <QPainter>
#include <QPushButton>
#include <QRegularExpression>
#include <QScrollArea>
#include <QScrollBar>
#include <QSlider>
#include <QSpinBox>
#include <QVBoxLayout>

namespace cdng {
CropNavigator::CropNavigator(QSize full, QImage image, QWidget *parent) : QWidget(parent), image_(std::move(image)), full_(full) {
    setMinimumHeight(150); setMaximumHeight(220); setCursor(Qt::CrossCursor);
    crop_ = QRect(QPoint((full.width() - 256) / 2, (full.height() - 256) / 2), QSize(256, 256));
}
QRect CropNavigator::imageRect() const {
    const auto fitted = full_.scaled(size(), Qt::KeepAspectRatio);
    return QRect((width() - fitted.width()) / 2, (height() - fitted.height()) / 2, fitted.width(), fitted.height());
}
void CropNavigator::setImage(QImage image) { image_ = std::move(image); update(); }
void CropNavigator::setCropSize(QSize size) {
    const auto centre = crop_.center();
    size = size.boundedTo(full_);
    crop_ = QRect(qBound(0, centre.x() - size.width() / 2, full_.width() - size.width()),
                  qBound(0, centre.y() - size.height() / 2, full_.height() - size.height()), size.width(), size.height());
    update(); emit cropChanged();
}
void CropNavigator::moveTo(QPoint position) {
    const auto target = imageRect(); if (target.isEmpty()) return;
    const int x = qBound(0, (position.x() - target.x()) * full_.width() / target.width() - crop_.width() / 2, full_.width() - crop_.width());
    const int y = qBound(0, (position.y() - target.y()) * full_.height() / target.height() - crop_.height() / 2, full_.height() - crop_.height());
    crop_.moveTopLeft({x, y}); update(); emit cropChanged();
}
void CropNavigator::mousePressEvent(QMouseEvent *event) { if (event->button() == Qt::LeftButton) moveTo(event->pos()); }
void CropNavigator::mouseMoveEvent(QMouseEvent *event) { if (event->buttons() & Qt::LeftButton) moveTo(event->pos()); }
void CropNavigator::paintEvent(QPaintEvent *) {
    QPainter painter(this); painter.fillRect(rect(), QColor("#111316"));
    const auto target = imageRect();
    if (!image_.isNull()) { painter.setRenderHint(QPainter::SmoothPixmapTransform); painter.drawImage(target, image_); }
    const QRect selected(target.x() + crop_.x() * target.width() / full_.width(), target.y() + crop_.y() * target.height() / full_.height(),
                         qMax(1, crop_.width() * target.width() / full_.width()), qMax(1, crop_.height() * target.height() / full_.height()));
    painter.setPen(QPen(QColor("#e3a04a"), 2)); painter.drawRect(selected);
}
NoiseDialog::NoiseDialog(QString backend, QString folder, int frame, int total, QSize size, QImage overview,
                         ComparisonSettings settings, QWidget *parent)
    : QDialog(parent), folder_(std::move(folder)), frame_(frame), size_(size),
      native_(settings.resolutions.isEmpty() ? size : settings.resolutions.first().size) {
    setWindowTitle(t("Compare original and result")); setModal(true); resize(1060, 800);
    auto *layout = new QVBoxLayout(this); layout->setSpacing(10);
    auto *hint = new QLabel(t("Drag the crop to move it. Choose its size and the zoom with the controls."), this); hint->setWordWrap(true); layout->addWidget(hint);
    // El recuadro va siempre sobre el original, a su resolución; el resultado toma la misma zona a la suya.
    navigator_ = new CropNavigator(native_, std::move(overview), this); navigator_->setObjectName("noiseNavigator");
    // Fotograma: el ruido cambia a lo largo de la toma, así que se puede comparar en cualquier punto.
    auto *frameRow = new QHBoxLayout;
    frameSlider_ = new QSlider(Qt::Horizontal, this); frameSlider_->setObjectName("noiseFrame");
    frameSlider_->setRange(0, qMax(0, total - 1)); frameSlider_->setValue(frame); frameRow->addWidget(frameSlider_, 1);
    frameNumber_ = new QLabel(this); frameNumber_->setMinimumWidth(90); frameNumber_->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    frameRow->addWidget(frameNumber_);
    const auto showFrame = [this, total] { frameNumber_->setText(QString("%1 / %2").arg(frame_ + 1).arg(total)); };
    showFrame();
    auto *controls = new QHBoxLayout; controls->setSpacing(6);
    controls->addWidget(new QLabel(t("Crop size"), this));
    cropSize_ = new QComboBox(this); cropSize_->setObjectName("noiseCropSize");
    for (const auto &preset : QList<QSize>{{128, 128}, {192, 192}, {256, 256}, {384, 384}, {512, 512}, {384, 216}, {512, 288}}) {
        const bool wide = preset.width() != preset.height();
        cropSize_->addItem(QString("%1 × %2%3").arg(preset.width()).arg(preset.height()).arg(wide ? " · 16:9" : ""), preset);
    }
    cropSize_->setCurrentIndex(2); controls->addWidget(cropSize_);
    controls->addSpacing(12); controls->addWidget(new QLabel(t("Zoom"), this));
    zoom_ = new QComboBox(this); zoom_->setObjectName("noiseZoom");
    for (int factor = 1; factor <= 4; ++factor) zoom_->addItem(QString("%1×").arg(factor), factor);
    zoom_->setCurrentIndex(1); zoom_->setToolTip(t("Pixel zoom of both crops, without smoothing")); controls->addWidget(zoom_); controls->addSpacing(12);
    // Resolución de salida: las dos imágenes se revelan a ese tamaño (Lanczos en lineal), como en la conversión.
    resolution_ = new QComboBox(this); resolution_->setObjectName("compareResolution");
    for (const auto &option : settings.resolutions) resolution_->addItem(option.label, option.key);
    if (settings.resolutions.isEmpty()) resolution_->addItem(QString("%1 × %2").arg(size.width()).arg(size.height()), "original");
    resolution_->setCurrentIndex(qMax(0, resolution_->findData(settings.resolution)));
    resolution_->setToolTip(t("Linear-light Lanczos. Centre crop when the aspect ratio changes. Minimum 1280×720."));

    // Segunda fila: lo que se aplica al convertir, en el orden de la conversión (reducción de ruido y compresión).
    auto *processing = new QHBoxLayout; processing->setSpacing(6);
    // Los mismos presets que la ventana; luma y croma solo se editan en Personalizada.
    presets_ = new QButtonGroup(this);
    auto *presetBox = new QWidget(this); auto *presetRow = new QHBoxLayout(presetBox); presetRow->setContentsMargins(0, 0, 0, 0); presetRow->setSpacing(0);
    const QStringList presetNames{t("Off"), t("Soft"), t("Medium"), t("Strong"), t("Custom")};
    for (int i = 0; i < presetNames.size(); ++i) {
        auto *button = new QPushButton(presetNames[i], presetBox); button->setCheckable(true); button->setProperty("segment", true);
        button->setObjectName(QString("comparePreset%1").arg(i)); presets_->addButton(button, i); presetRow->addWidget(button);
    }
    int preset = settings.preset;
    if (preset < 0 || preset > CustomNoise) preset = CustomNoise;
    if (preset < CustomNoise && (NoisePresets[preset][0] != settings.luma || NoisePresets[preset][1] != settings.chroma)) preset = CustomNoise;
    presets_->button(preset)->setChecked(true);
    processing->addWidget(presetBox);
    QList<QSpinBox *> boxes;
    for (int i = 0; i < 2; ++i) {
        processing->addSpacing(i == 0 ? 6 : 12); processing->addWidget(new QLabel(i == 0 ? t("Luma") : t("Chroma"), this));
        auto *box = new QSpinBox(this); box->setObjectName(i == 0 ? "compareLuma" : "compareChroma");
        box->setRange(0, 100); box->setSingleStep(5); box->setSuffix(" %");
        box->setValue(int(i == 0 ? settings.luma : settings.chroma)); processing->addWidget(box); boxes.append(box);
    }
    luma_ = boxes[0]; chroma_ = boxes[1];
    const auto showPreset = [this] { for (auto *box : {luma_, chroma_}) box->setEnabled(presets_->checkedId() == CustomNoise); };
    showPreset();
    connect(presets_, &QButtonGroup::idClicked, this, [this, showPreset](int id) {
        if (id < CustomNoise) {   // un solo cálculo: los dos valores cambian sin disparar dos peticiones
            QSignalBlocker blockLuma(luma_), blockChroma(chroma_);
            luma_->setValue(NoisePresets[id][0]); chroma_->setValue(NoisePresets[id][1]);
            status_->setText(t("Rendering comparison…")); timer_.start();
        }
        showPreset();
    });
    processing->addStretch();
    auto *packing = new QHBoxLayout; packing->setSpacing(6);
    packing->addWidget(new QLabel(t("Resolution"), this)); packing->addWidget(resolution_); packing->addSpacing(18);
    packing->addWidget(new QLabel(t("Compression"), this));
    compression_ = new QComboBox(this); compression_->setObjectName("compareCompression");
    compression_->addItem(t("None"), "none"); compression_->addItem(t("Ratio"), "ratio"); compression_->addItem(t("Quality"), "quality");
    compression_->setToolTip(t("“None” only shows the effect of noise reduction; the BRAW is always compressed."));
    ratio_ = new QComboBox(this); ratio_->setObjectName("compareRatio");
    for (int r : {3, 4, 5, 6, 8, 10, 12}) ratio_->addItem(QString("%1:1").arg(r), r);
    quality_ = new QComboBox(this); quality_->setObjectName("compareQ");
    for (const auto &level : qualityLevels()) quality_->addItem(level.name, level.q);
    quality_->setToolTip(t("Q0 = finer · Q5 = smaller"));
    compression_->setCurrentIndex(qMax(0, compression_->findData(settings.compression)));
    ratio_->setCurrentIndex(qMax(0, ratio_->findData(settings.ratio))); quality_->setCurrentIndex(qMax(0, quality_->findData(nearestQualityLevel(settings.q).q)));
    packing->addWidget(compression_); packing->addWidget(ratio_); packing->addWidget(quality_); packing->addStretch();
    const auto showCompression = [this] { const auto mode = compression_->currentData().toString(); ratio_->setVisible(mode == "ratio"); quality_->setVisible(mode == "quality"); };
    showCompression();
    controls->addWidget(new QLabel(t("Exposure"), this));
    exposure_ = new QDoubleSpinBox(this); exposure_->setObjectName("noiseExposure");
    exposure_->setRange(0, 4); exposure_->setSingleStep(.5); exposure_->setDecimals(1); exposure_->setPrefix("+"); exposure_->setSuffix(" EV");
    exposure_->setToolTip(t("Viewing only: brightens both images after noise reduction, as when lifting shadows in grading. It does not change the conversion."));
    controls->addWidget(exposure_); controls->addStretch();
    // Arriba, en dos columnas: los controles con su etiqueta a la izquierda y el mapa (la imagen completa con el
    // recuadro) a la derecha, con la proporción del original.
    auto *top = new QHBoxLayout; top->setSpacing(18);
    auto *form = new QGridLayout; form->setHorizontalSpacing(12); form->setVerticalSpacing(8);
    const QList<std::pair<QString, QLayout *>> rows{{t("Frame"), frameRow}, {t("View"), controls}, {t("Noise reduction"), processing}, {t("Format"), packing}};
    for (int row = 0; row < rows.size(); ++row) {
        auto *title = new QLabel(rows[row].first, this); title->setStyleSheet("font-weight:600;");
        form->addWidget(title, row, 0, Qt::AlignLeft | Qt::AlignVCenter); form->addLayout(rows[row].second, row, 1);
    }
    form->setColumnStretch(1, 1); form->setRowStretch(rows.size(), 1);
    top->addLayout(form, 1);
    const int mapHeight = 180;
    navigator_->setFixedSize(qBound(mapHeight, int(mapHeight * double(native_.width()) / qMax(1, native_.height())), 420), mapHeight);
    top->addWidget(navigator_, 0, Qt::AlignTop);
    layout->addLayout(top);
    auto *panes = new QGridLayout;
    beforeTitle_ = new QLabel(this); beforeTitle_->setObjectName("compareBeforeTitle"); panes->addWidget(beforeTitle_, 0, 0);
    afterTitle_ = new QLabel(this); afterTitle_->setObjectName("compareAfterTitle"); panes->addWidget(afterTitle_, 0, 1);
    QList<QScrollArea *> views; QList<QLabel *> images;
    for (int i = 0; i < 2; ++i) {
        auto *view = new QScrollArea(this); view->setAlignment(Qt::AlignCenter); view->setMinimumHeight(200);
        view->setStyleSheet("QScrollArea { background:#111316; border:1px solid #484b53; }");
        auto *image = new QLabel(view); image->setObjectName(i == 0 ? "noiseBefore" : "noiseAfter"); image->setAlignment(Qt::AlignCenter);
        image->setFixedSize(256, 256); view->setWidget(image); views.append(view); images.append(image); panes->addWidget(view, 1, i);
    }
    for (int i = 0; i < 2; ++i) {
        connect(views[i]->horizontalScrollBar(), &QScrollBar::valueChanged, this, [this, i] { followView(i); });
        connect(views[i]->verticalScrollBar(), &QScrollBar::valueChanged, this, [this, i] { followView(i); });
    }
    views_ = views; before_ = images[0]; after_ = images[1]; layout->addLayout(panes, 1);
    status_ = new QLabel(this); status_->setObjectName("noiseStatus"); status_->setWordWrap(true); layout->addWidget(status_);
    auto *note = new QLabel(t("Same noise filter and encoder as the converter; the result is decoded as Blackmagic RAW does (measured against its SDK). Display colour is approximate."), this);
    note->setWordWrap(true); layout->addWidget(note);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Apply | QDialogButtonBox::Close, this); layout->addWidget(buttons);
    connect(buttons->button(QDialogButtonBox::Apply), &QAbstractButton::clicked, this, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    service_ = new PreviewService(std::move(backend), this);
    timer_.setSingleShot(true); timer_.setInterval(180);
    connect(&timer_, &QTimer::timeout, this, &NoiseDialog::request);
    connect(navigator_, &CropNavigator::cropChanged, this, [this] { status_->setText(t("Rendering comparison…")); timer_.start(); });
    for (auto *box : boxes) connect(box, qOverload<int>(&QSpinBox::valueChanged), this, [this, showPreset] {
        // Un valor que no es el del preset marcado pasa a Personalizada.
        const int id = presets_->checkedId();
        if (id < CustomNoise && (luma_->value() != NoisePresets[id][0] || chroma_->value() != NoisePresets[id][1])) {
            presets_->button(CustomNoise)->setChecked(true); showPreset();
        }
        status_->setText(t("Rendering comparison…")); timer_.start();
    });
    connect(frameSlider_, &QSlider::valueChanged, this, [this, showFrame](int value) {
        frame_ = value; showFrame(); status_->setText(t("Rendering comparison…")); timer_.start();
    });
    const auto changed = [this] { status_->setText(t("Rendering comparison…")); timer_.start(); };
    connect(resolution_, qOverload<int>(&QComboBox::currentIndexChanged), this, [this, settings](int index) {
        if (index >= 0 && index < settings.resolutions.size()) { size_ = settings.resolutions[index].size; status_->setText(t("Rendering comparison…")); timer_.start(); }
    });
    connect(compression_, qOverload<int>(&QComboBox::currentIndexChanged), this, [showCompression, changed] { showCompression(); changed(); });
    connect(ratio_, qOverload<int>(&QComboBox::currentIndexChanged), this, changed);
    connect(quality_, qOverload<int>(&QComboBox::currentIndexChanged), this, changed);
    connect(exposure_, qOverload<double>(&QDoubleSpinBox::valueChanged), this, [this] { status_->setText(t("Rendering comparison…")); timer_.start(); });
    connect(cropSize_, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] { navigator_->setCropSize(cropSize_->currentData().toSize()); });
    connect(zoom_, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] { refreshPanes(); });
    connect(service_, &PreviewService::error, status_, &QLabel::setText);
    connect(service_, &PreviewService::noiseReady, this, [this](const QJsonObject &meta, const QImage &before, const QImage &after, const QImage &overview) {
        beforeImage_ = before; afterImage_ = after; refreshPanes(); navigator_->setImage(overview);
        const auto roi = meta.value("roi").toArray();
        const auto compression = meta.value("compression").toObject();
        const auto out = meta.value("result_roi").toArray();
        QString text = QString("%1 × %2 · x %3 · y %4").arg(roi[2].toInt()).arg(roi[3].toInt()).arg(roi[0].toInt()).arg(roi[1].toInt());
        if (out.size() == 4 && (out[2].toInt() != roi[2].toInt() || out[3].toInt() != roi[3].toInt()))
            text += " · " + QString(t("same area in the result: %1 × %2 px")).arg(out[2].toInt()).arg(out[3].toInt());
        if (!compression.isEmpty())
            text += " · " + QString(t("compressed at q %1 · %2:1 for this frame")).arg(compression.value("q").toInt()).arg(compression.value("ratio").toDouble(), 0, 'f', 1);
        else if (noise() == QList<double>{0., 0.}) text += " · " + t("Nothing applied: both images are identical.");
        status_->setText(text);
    });
    QTimer::singleShot(0, this, &NoiseDialog::request);
}
void NoiseDialog::refreshPanes() {
    // Nearest-neighbour scaling: the point is to see the grain, not a smoothed version of it.
    // El zoom va sobre el original; el resultado se muestra al mismo tamaño en pantalla (la misma zona de la escena),
    // así que con menos resolución se ve más pixelado.
    const int factor = qMax(1, zoom_->currentData().toInt());
    const QSize shown = (beforeImage_.isNull() ? afterImage_.size() : beforeImage_.size()) * factor;
    for (auto [label, image] : {std::pair{before_, beforeImage_}, std::pair{after_, afterImage_}}) {
        if (image.isNull()) continue;
        const auto scaled = image.size() == shown ? image : image.scaled(shown, Qt::IgnoreAspectRatio, Qt::FastTransformation);
        label->setFixedSize(scaled.size()); label->setPixmap(QPixmap::fromImage(scaled));
    }
    applyCentre();
}
void NoiseDialog::followView(int from) {
    if (syncing_ || views_.size() != 2) return;
    // Centro visible del panel movido, como fracción de su imagen; el otro panel se centra en el mismo punto.
    const auto *h = views_[from]->horizontalScrollBar(), *v = views_[from]->verticalScrollBar();
    const auto *content = views_[from]->widget();
    if (!content || content->width() <= 0 || content->height() <= 0) return;
    centre_ = {(h->value() + views_[from]->viewport()->width() / 2.0) / content->width(),
               (v->value() + views_[from]->viewport()->height() / 2.0) / content->height()};
    applyCentre();
}
void NoiseDialog::applyCentre() {
    syncing_ = true;
    for (auto *view : views_) {
        const auto *content = view->widget();
        view->horizontalScrollBar()->setValue(int(std::lround(centre_.x() * content->width() - view->viewport()->width() / 2.0)));
        view->verticalScrollBar()->setValue(int(std::lround(centre_.y() * content->height() - view->viewport()->height() / 2.0)));
    }
    syncing_ = false;
}
QList<double> NoiseDialog::noise() const { return {double(luma_->value()), double(chroma_->value())}; }
ComparisonSettings NoiseDialog::settings() const {
    ComparisonSettings out{double(luma_->value()), double(chroma_->value()), presets_->checkedId(), compression_->currentData().toString(), ratio_->currentData().toInt(), quality_->currentData().toInt()};
    out.resolution = resolution_->currentData().toString();
    return out;
}
void NoiseDialog::request() {
    beforeTitle_->setText(QString(t("Original · as recorded in the DNG (%1 × %2)")).arg(native_.width()).arg(native_.height()));
    const auto mode = compression_->currentData().toString();
    const QString what = noise() == QList<double>{0., 0.} ? QString() : QString(t("noise reduction %1/%2 %")).arg(luma_->value()).arg(chroma_->value());
    const QString packed = mode == "ratio" ? ratio_->currentText() : mode == "quality" ? quality_->currentText() : QString();
    afterTitle_->setText(what.isEmpty() && packed.isEmpty() && size_ == native_ ? t("Result · nothing applied")
        : t("Result") + " · " + QStringList({size_ == native_ ? QString() : QString("%1 × %2").arg(size_.width()).arg(size_.height()), what,
              packed.isEmpty() ? QString() : t("compression") + " " + packed}).filter(QRegularExpression(".")).join(" + "));
    status_->setText(t("Rendering comparison…"));
    service_->requestNoise(folder_, frame_, size_, navigator_->crop(), noise(), exposure_->value(), mode, ratio_->currentData().toInt(), quality_->currentData().toInt());
}
}
