#include "i18n.h"
#include "noise_dialog.h"
#include <QDateTime>
#include "converter_window.h"
#include <QApplication>
#include <QButtonGroup>
#include <QCheckBox>
#include <QCloseEvent>
#include <QComboBox>
#include <QDesktopServices>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QMimeData>
#include <QDir>
#include <QFileDialog>
#include <QFileInfo>
#include <QFontDatabase>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QLineEdit>
#include <QLocale>
#include <QMenuBar>
#include <QMessageBox>
#include <QPixmap>
#include <QPlainTextEdit>
#include <QProgressBar>
#include <QPushButton>
#include <QDialog>
#include <QDialogButtonBox>
#include <QTextBrowser>
#include <QWidget>
#include <QFile>
#include <QRadioButton>
#include <QScrollArea>
#include <QSignalBlocker>
#include <QSlider>
#include <QSplitter>
#include <QSpinBox>
#include <QThread>
#include <QTreeWidget>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>
#include <cmath>

namespace cdng {
namespace {
QLabel *label(const QString &text, QWidget *parent, const char *role = nullptr) {
    auto *value = new QLabel(text, parent); value->setWordWrap(true);
    if (role) value->setProperty("role", role);
    return value;
}
// Duración legible: segundos por debajo del minuto, y la coma decimal en español.
QString duration(double seconds) {
    const QLocale locale(currentLanguage() == "es" ? QLocale::Spanish : QLocale::English);
    return seconds < 60 ? QString(t("%1 s")).arg(locale.toString(seconds, 'f', 1))
                        : QString(t("%1 min")).arg(locale.toString(seconds / 60, 'f', 1));
}
QVBoxLayout *card(QWidget *parent, const QString &title, QFrame **frame = nullptr) {
    auto *box = new QFrame(parent); box->setObjectName("card");
    auto *layout = new QVBoxLayout(box); layout->setContentsMargins(14, 12, 14, 14); layout->setSpacing(10);
    layout->addWidget(label(title.toUpper(), box, "heading"));
    if (frame) *frame = box;
    return layout;
}
QLabel *metadata(QGridLayout *grid, int row, int column, const QString &title, const char *name) {
    auto *box = new QWidget(grid->parentWidget()); auto *layout = new QVBoxLayout(box);
    layout->setContentsMargins(0, 0, 0, 0); layout->setSpacing(3);
    layout->addWidget(label(title, box, "muted"));
    auto *value = label("—", box, "value"); value->setObjectName(name); layout->addWidget(value);
    grid->addWidget(box, row, column, Qt::AlignTop); return value;
}
QWidget *segments(QButtonGroup *group, const QStringList &names, const QList<int> &ids, QWidget *parent, const QString &prefix) {
    auto *box = new QWidget(parent); auto *row = new QHBoxLayout(box); row->setContentsMargins(0, 0, 0, 0); row->setSpacing(0);
    for (int i = 0; i < names.size(); ++i) {
        auto *button = new QPushButton(names[i], box); button->setCheckable(true); button->setProperty("segment", true);
        button->setObjectName(prefix + QString::number(ids[i])); group->addButton(button, ids[i]); row->addWidget(button);
    }
    return box;
}
}
ConverterWindow::ConverterWindow(ProfileStore store, QString backend, QWidget *parent)
    : QWidget(parent), store_(std::move(store)) {
    setObjectName("converterRoot"); setWindowTitle(t("CDNG to BRAW Converter"));
    resize(1120, 914); setAcceptDrops(true);   // con dos columnas iguales, la vista previa sale a 16:9
    auto baseFont = font(); baseFont.setPixelSize(13); setFont(baseFont);
    const bool dark = palette().color(QPalette::Window).lightness() < 128;
    const QString bg = dark ? "#26272b" : "#eceef1", panel = dark ? "#2e3034" : "#f7f8fa";
    const QString field = dark ? "#35373c" : "#ffffff", line = dark ? "#484b53" : "#c9ccd3";
    const QString fg = dark ? "#e6e7ea" : "#1c1f24", muted = dark ? "#a7acb7" : "#687083";
    const QString accent = dark ? "#dfa049" : "#b86a12";
    setStyleSheet(QString(R"CSS(
        QWidget { font-size:13px; }
        QWidget#converterRoot { background:%1; color:%5; }
        QWidget#converterBody, QScrollArea { background:%1; border:0; }
        QFrame#card, QFrame#header, QFrame#footer { background:%2; border:1px solid %4; border-radius:8px; }
        QFrame#header, QFrame#footer { border-radius:0; }
        QLabel, QCheckBox, QRadioButton { color:%5; background:transparent; }
        QLabel[role="heading"] { color:%6; font-size:11px; font-weight:600; letter-spacing:1px; }
        QLabel[role="muted"] { color:%6; font-size:11px; }
        QLabel[role="value"] { font-family:Menlo,Consolas,monospace; font-size:11px; }
        QLabel[role="badge"] { border:1px solid %7; border-radius:10px; color:%7; padding:4px 10px; font-size:11px; }
        QLineEdit, QSpinBox, QComboBox, QPlainTextEdit { background:%3; color:%5; border:1px solid %4; border-radius:5px; padding:5px 7px; selection-background-color:%7; }
        QLineEdit { font-family:Menlo,Consolas,monospace; font-size:11px; min-height:18px; }
        QSpinBox, QComboBox { padding-right:22px; }
        QSpinBox::up-button { subcontrol-origin:border; subcontrol-position:top right; width:18px; border-left:1px solid %4; }
        QSpinBox::down-button { subcontrol-origin:border; subcontrol-position:bottom right; width:18px; border-left:1px solid %4; }
        QSpinBox::up-arrow { image:url(:/ui/up.svg); width:8px; height:5px; }
        QSpinBox::down-arrow { image:url(:/ui/down.svg); width:8px; height:5px; }
        QComboBox::drop-down { subcontrol-origin:padding; subcontrol-position:top right; width:18px; border-left:1px solid %4; }
        QComboBox::down-arrow { image:url(:/ui/down.svg); width:8px; height:5px; }
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color:%7; }
        QPushButton { background:%3; color:%5; border:1px solid %4; border-radius:5px; padding:6px 11px; }
        QPushButton:hover { border-color:%7; }
        QPushButton:disabled, QSpinBox:disabled, QComboBox:disabled { color:%6; background:%1; }
        QPushButton[segment="true"] { border-radius:0; padding:6px 7px; font-size:11px; }
        QPushButton[segment="true"]:checked, QPushButton#startConversion { background:%7; color:white; border-color:%7; font-weight:600; }
        QPushButton#startConversion:disabled { background:%4; color:%6; border-color:%4; }
        QPushButton[segment="true"]:checked:disabled { background:%4; color:%6; border-color:%4; font-weight:600; }
        QRadioButton::indicator { width:12px; height:12px; border:1px solid %4; border-radius:7px; background:%3; }
        QRadioButton::indicator:checked { border-color:%7; background:%7; }
        QCheckBox::indicator { width:13px; height:13px; border:1px solid %4; border-radius:3px; background:%3; }
        QCheckBox::indicator:checked { border-color:%7; background:%7; image:url(:/ui/check.svg); }
        QCheckBox:disabled, QRadioButton:disabled { color:%6; }
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { background:%1; border-color:%4; }
        QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled { background:%6; border-color:%6; }
        QTreeWidget { background:%3; color:%5; border:1px solid %4; border-radius:5px; font-size:11px; }
        QTreeWidget::item { padding:2px 0; }
        QTreeWidget::item:selected { background:%7; color:white; }
        QTreeWidget::indicator { width:12px; height:12px; border:1px solid %4; border-radius:3px; background:%3; }
        QTreeWidget::indicator:checked { border-color:%7; background:%7; image:url(:/ui/check.svg); }
        QHeaderView::section { background:%2; color:%6; border:0; border-bottom:1px solid %4; padding:4px 6px; font-size:10px; font-weight:600; }
        QProgressBar { border:1px solid %4; background:%3; border-radius:3px; min-height:6px; max-height:6px; }
        QProgressBar::chunk { background:%7; border-radius:2px; }
        QSlider::groove:horizontal { height:5px; border-radius:2px; background:%4; }
        QSlider::sub-page:horizontal { background:%7; border-radius:2px; }
        QSlider::handle:horizontal { width:13px; height:13px; margin:-4px 0; background:%7; border-radius:6px; }
    )CSS").arg(bg, panel, field, line, fg, muted, accent));
    auto *layout = new QVBoxLayout(this); layout->setContentsMargins(0, 0, 0, 0); layout->setSpacing(0);
    auto *header = new QFrame(this); header->setObjectName("header");
    auto *headerRow = new QHBoxLayout(header); headerRow->setContentsMargins(18, 11, 18, 11);
    auto *name = label("cdng2braw", header); auto font = name->font(); font.setBold(true); name->setFont(font);
    name->setWordWrap(false);
    // Arriba a la izquierda, el icono y el nombre; la descripción y la web van en el pie.
    auto *logo = new QLabel(header); logo->setObjectName("appIcon");
    const qreal ratio = devicePixelRatioF();
    QPixmap icon = QPixmap(":/ui/icon.png").scaled(QSize(28, 28) * ratio, Qt::KeepAspectRatio, Qt::SmoothTransformation);
    icon.setDevicePixelRatio(ratio); logo->setPixmap(icon); logo->setFixedSize(28, 28);
    auto *about = new QPushButton(t("About…"), header); about->setObjectName("about");
    connect(about, &QPushButton::clicked, this, &ConverterWindow::showAbout);
    // Perfiles de cámara: la entrada global (instalar en el equipo de edición, desinstalar), sin depender del lote.
    // La fila de estado del origen es la contextual. Igual en macOS y en Windows: un botón en la cabecera.
    // Hoy solo hay un perfil que gestionar (la Pocket original), así que es un botón y no un menú desplegable.
    auto *cameraProfiles = new QPushButton(t("Camera profile…"), header); cameraProfiles->setObjectName("cameraProfiles");
#ifdef Q_OS_MACOS
    // En macOS, «Acerca de» va además en el menú de la app (rol AboutRole).
    auto *menuBar = new QMenuBar(this);
    auto *aboutAction = menuBar->addMenu(t("Help"))->addAction(t("About CDNG to BRAW Converter"));
    aboutAction->setMenuRole(QAction::AboutRole);
    connect(aboutAction, &QAction::triggered, this, &ConverterWindow::showAbout);
    if (!menuBar->isNativeMenuBar()) delete menuBar;   // sin barra nativa (p. ej. offscreen) se dibujaría sobre la cabecera
#endif
    language_ = new QComboBox(header); language_->setObjectName("uiLanguage"); language_->setAccessibleName(t("Language"));
    language_->addItem(t("System language"), "system"); language_->addItem("English", "en"); language_->addItem("Español", "es");
    language_->setCurrentIndex(qMax(0, language_->findData(languagePreference())));
    const QString creditText = QString("CinemaDNG → Blackmagic RAW · <a href=\"https://www.elcacharrista.com\" style=\"color:%1; text-decoration:none\">www.elcacharrista.com</a>").arg(accent);
    name->setText(QCoreApplication::applicationName());
    auto nameFont = name->font(); nameFont.setPixelSize(15); name->setFont(nameFont);
    headerRow->addWidget(logo); headerRow->addSpacing(4); headerRow->addWidget(name); headerRow->addStretch(); headerRow->addWidget(cameraProfiles); headerRow->addWidget(about); headerRow->addWidget(language_); layout->addWidget(header);

    // Sin barra de desplazamiento: el tamaño mínimo de la ventana es el de su contenido, así nada queda oculto.
    layout->setSizeConstraint(QLayout::SetMinimumSize);
    auto *body = new QWidget(this); body->setObjectName("converterBody");
    auto *columns = new QHBoxLayout(body); columns->setContentsMargins(14, 14, 14, 14); columns->setSpacing(0);
    // Dos columnas del mismo ancho; el separador se puede arrastrar.
    auto *split = new QSplitter(Qt::Horizontal, body); split->setObjectName("columns"); split->setChildrenCollapsible(false); split->setHandleWidth(14);
    auto *leftColumn = new QWidget(split); auto *rightColumn = new QWidget(split);
    auto *left = new QVBoxLayout(leftColumn); auto *right = new QVBoxLayout(rightColumn);
    left->setContentsMargins(0, 0, 0, 0); right->setContentsMargins(0, 0, 0, 0); left->setSpacing(12); right->setSpacing(12);
    split->setStretchFactor(0, 1); split->setStretchFactor(1, 1); split->setSizes({1000, 1000});
    columns->addWidget(split);
    layout->addWidget(body, 1);

    // Las dos columnas acaban a la misma altura: a la izquierda crece la vista previa; a la derecha, la tarjeta Avanzado.
    QFrame *sourceCard; auto *source = card(body, t("Source"), &sourceCard); left->addWidget(sourceCard);
    input_ = new QLineEdit(sourceCard); input_->setObjectName("inputFolder"); input_->setPlaceholderText(t("Folder with CinemaDNG takes · or drop it anywhere in this window"));
    input_->setAcceptDrops(false);   // the whole window takes the drop (QLineEdit would paste the URL as text)
    chooseInput_ = new QPushButton(t("Browse…"), sourceCard);
    auto *inputRow = new QHBoxLayout; inputRow->addWidget(input_, 1); inputRow->addWidget(chooseInput_); source->addLayout(inputRow);
    // Lote: cada subcarpeta con una secuencia DNG es una toma; las marcadas se convierten con los mismos ajustes.
    clips_ = new QTreeWidget(sourceCard); clips_->setObjectName("clipList");
    clips_->setColumnCount(5); clips_->setRootIsDecorated(false); clips_->setUniformRowHeights(true); clips_->setAlternatingRowColors(false);
    // La resolución va por toma: los niveles fijan el ancho y la altura sale de la proporción de cada una, así que
    // en un lote que mezcle 16:9 y open gate se ve de un vistazo que cada toma conserva la suya.
    clips_->setHeaderLabels({t("Take"), t("Camera"), t("Duration"), t("Output"), t("Status")});
    clips_->setMinimumHeight(118); clips_->setMaximumHeight(160);
    clips_->setTextElideMode(Qt::ElideMiddle); clips_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    clips_->header()->setStretchLastSection(false); clips_->header()->setSectionResizeMode(QHeaderView::ResizeToContents);
    clips_->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    source->addWidget(clips_);
    // Marcar o desmarcar todas de golpe: con una tarjeta de muchas tomas, ir una a una es tedioso. Solo tocan las
    // que se pueden convertir; las no admitidas no son marcables.
    auto *selectRow = new QHBoxLayout; selectRow->setContentsMargins(0, 0, 0, 0); selectRow->setSpacing(8);
    selectAll_ = new QPushButton(t("Select all"), sourceCard); selectAll_->setObjectName("selectAllClips");
    selectNone_ = new QPushButton(t("Select none"), sourceCard); selectNone_->setObjectName("selectNoneClips");
    for (auto *button : {selectAll_, selectNone_}) button->setFlat(true);
    selectRow->addWidget(selectAll_); selectRow->addWidget(selectNone_); selectRow->addStretch();
    source->addLayout(selectRow);
    profileRow_ = new QWidget(sourceCard); auto *profileRow = new QHBoxLayout(profileRow_); profileRow->setContentsMargins(0, 0, 0, 0);
    profileState_ = label(QString(), profileRow_); profileState_->setObjectName("converterProfileStatus");
    profileButton_ = new QPushButton(t("Manage profile…"), profileRow_);
    profileRow->addWidget(profileState_, 1); profileRow->addWidget(profileButton_); profileRow_->hide(); source->addWidget(profileRow_);
    sourceCompatibility_ = label(t("Choose a folder: every take inside it is found, including subfolders."), sourceCard, "muted");
    sourceCompatibility_->setObjectName("sourceCompatibility"); source->addWidget(sourceCompatibility_);

    QFrame *previewCard; auto *previewLayout = card(body, t("Preview"), &previewCard); left->addWidget(previewCard, 1);
    preview_ = new PreviewCanvas(previewCard); preview_->setObjectName("previewCanvas"); previewLayout->addWidget(preview_, 1);
    auto *scrub = new QHBoxLayout; frame_ = new QSlider(Qt::Horizontal, previewCard); frame_->setObjectName("previewFrame");
    frame_->setRange(0, 0); frame_->setEnabled(false); frameNumber_ = label("— / —", previewCard, "value"); frameNumber_->setMinimumWidth(65);
    scrub->addWidget(frame_, 1); scrub->addWidget(frameNumber_); previewLayout->addLayout(scrub);
    clipDetails_ = label("—", previewCard, "value"); clipDetails_->setObjectName("clipDetails"); clipDetails_->setWordWrap(true); previewLayout->addWidget(clipDetails_);
    // El comparador es de todo el proceso (reducción de ruido y compresión), así que vive junto a la vista previa.
    auto *previewFooter = new QHBoxLayout;
    previewState_ = label(t("Approximate DNG preview · capture WB, exposure and crop."), previewCard, "muted"); previewFooter->addWidget(previewState_, 1);
    compareNoise_ = new QPushButton(t("Compare original and result…"), previewCard); compareNoise_->setObjectName("compareNoise");
    compareNoise_->setToolTip(t("Crop at 100 % before and after noise reduction and compression, decoded as Blackmagic RAW does."));
    previewFooter->addWidget(compareNoise_); previewLayout->addLayout(previewFooter);
    previewCard->setToolTip(t("Compression and noise reduction are not shown here: use the comparison. Colour may differ from Resolve."));

    // Formato: compresión (ratio o calidad) y resolución en una sola tarjeta.
    QFrame *compressionBox; auto *compression = card(body, t("Format"), &compressionBox); compressionCard_ = compressionBox; right->addWidget(compressionBox);
    auto *ratioRow = new QHBoxLayout;
    ratioMode_ = new QRadioButton(t("Ratio"), compressionBox); ratioMode_->setObjectName("ratioMode"); ratioMode_->setChecked(true);
    ratioMode_->setToolTip(t("Constant ratio (target)"));
    ratios_ = new QButtonGroup(this);
    ratioRow->addWidget(ratioMode_);
    ratioRow->addWidget(segments(ratios_, {"3:1", "4:1", "5:1", "6:1", "8:1", "10:1", "12:1"}, {3, 4, 5, 6, 8, 10, 12}, compressionBox, "ratio"), 1);
    compression->addLayout(ratioRow);
    ratios_->button(5)->setChecked(true);
    auto *qualityRow = new QHBoxLayout; qualityMode_ = new QRadioButton(t("Quality"), compressionBox); qualityMode_->setObjectName("qualityMode");
    qualityMode_->setToolTip(t("Constant quality") + " · " + t("Q0 = finer · Q5 = smaller"));
    qualities_ = new QButtonGroup(this);
    QStringList qualityNames; QList<int> qualityIds;
    for (const auto &level : qualityLevels()) { qualityNames << level.name; qualityIds << level.q; }
    qualityButtons_ = segments(qualities_, qualityNames, qualityIds, compressionBox, "quality");
    qualityButtons_->setToolTip(t("Q0 = finer · Q5 = smaller"));
    qualities_->button(nearestQualityLevel(4).q)->setChecked(true);
    // q libre (0–24) como opción avanzada, en la misma fila: la casilla cambia los niveles por un desplegable con todas
    // las q del códec, y en las que son un nivel pone su nombre, para ver la equivalencia. Solo en calidad fija.
    qCustomCheck_ = new QCheckBox("q", compressionBox); qCustomCheck_->setObjectName("qCustomCheck");
    qCustomCheck_->setToolTip(t("Advanced: set the exact quantiser (q 0–24) instead of the levels."));
    qCustom_ = new QComboBox(compressionBox); qCustom_->setObjectName("qCustom");
    for (int q = 0; q <= 24; ++q) {
        const int level = qualityLevelIndex(q);
        qCustom_->addItem(level >= 0 ? QString("q%1 (%2)").arg(q).arg(qualityLevels()[level].name) : QString("q%1").arg(q), q);
    }
    qCustom_->setCurrentIndex(nearestQualityLevel(4).q); qCustom_->hide();
    estimate_ = label(t("Select a sequence to estimate its size."), compressionBox, "value"); estimate_->setObjectName("compressionEstimate");
    estimate_->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
    qualityRow->addWidget(qualityMode_); qualityRow->addWidget(qualityButtons_); qualityRow->addWidget(qCustom_);
    qualityRow->addWidget(qCustomCheck_); qualityRow->addWidget(estimate_, 1);
    compression->addLayout(qualityRow);
    estimate_->setToolTip(t("Estimate based on 12 bits per pixel. The q 0 limit may give a smaller file; the actual ratio depends on content."));

    QWidget *resolutionBox = compressionBox; auto *resolutionLayout = compression;
    auto *resolutionRow = new QHBoxLayout; resolutionRow->addWidget(label(t("Resolution"), resolutionBox));
    resolution_ = new QComboBox(resolutionBox); resolution_->setObjectName("outputResolution");
    // Los niveles fijan el **ancho**; la altura sale de la proporción del material, así que valen igual para
    // 16:9 y para open gate (3K de un 3:2 da 3072×2048, no 3072×1728 recortado). La altura resultante se
    // muestra al lado, en resolutionSize_. «Personalizado» sí fija las dos y entonces el motor recorta centrado.
    resolution_->addItem(t("Original"), "original");
    resolution_->addItem("UHD · 3840", "3840"); resolution_->addItem("3K · 3072", "3072");
    resolution_->addItem("2.7K · 2704", "2704"); resolution_->addItem("2.5K · 2560", "2560");
    resolution_->addItem("Full HD · 1920", "1920"); resolution_->addItem(t("Custom…"), "custom");
    resolutionSize_ = label("—", resolutionBox, "value"); resolutionSize_->setObjectName("resolutionSize");
    resolutionRow->addWidget(resolution_, 1); resolutionRow->addWidget(resolutionSize_); resolutionLayout->addLayout(resolutionRow);
    customSize_ = new QWidget(resolutionBox); auto *customRow = new QHBoxLayout(customSize_); customRow->setContentsMargins(0, 0, 0, 0);
    outputWidth_ = new QSpinBox(customSize_); outputHeight_ = new QSpinBox(customSize_);
    outputWidth_->setObjectName("outputWidth"); outputHeight_->setObjectName("outputHeight");
    outputWidth_->setRange(1280, 16384); outputHeight_->setRange(720, 16384);   // el mismo mínimo que el motor (1280×720)
    outputWidth_->setSingleStep(2); outputHeight_->setSingleStep(2); outputWidth_->setValue(3072); outputHeight_->setValue(1728);
    customRow->addWidget(outputWidth_); customRow->addWidget(label("×", customSize_)); customRow->addWidget(outputHeight_); customRow->addStretch();
    customSize_->hide(); resolutionLayout->addWidget(customSize_);
    resolution_->setToolTip(t("Linear-light Lanczos. The levels set the width and keep the aspect ratio of each take; "
                              "only a custom size crops the centre when the aspect ratio changes. Minimum 1280×720."));

    QFrame *noiseBox; auto *noise = card(body, t("Noise reduction"), &noiseBox); nrCard_ = noiseBox; right->addWidget(noiseBox);
    nrPresets_ = new QButtonGroup(this);
    noise->addWidget(segments(nrPresets_, {t("Off"), t("Soft"), t("Medium"), t("Strong"), t("Custom")}, {0, 1, 2, 3, 4}, noiseBox, "nrPreset"));
    nrPresets_->button(0)->setChecked(true);
    auto *noiseRow = new QHBoxLayout; noiseRow->setSpacing(8);
    QList<QSpinBox *> boxes;
    const QStringList noiseLabels{t("Luma"), t("Chroma")};
    const QStringList noiseNames{"nrLuma", "nrChroma"};
    for (int index = 0; index < 2; ++index) {
        noiseRow->addWidget(label(noiseLabels[index], noiseBox));
        auto *box = new QSpinBox(noiseBox); box->setObjectName(noiseNames[index]);
        box->setRange(0, 100); box->setSingleStep(5); box->setSuffix(" %");
        noiseRow->addWidget(box); boxes.append(box);
    }
    noiseRow->addStretch();
    nrLuma_ = boxes[0]; nrChroma_ = boxes[1];
    noiseBox->setToolTip(t("Light filtering inside each frame to prepare the image for compression: fewer artefacts from noise, detail preserved. Luma above 50 % starts to soften fine texture.") + "\n" + t("Leave temporal noise reduction to DaVinci Resolve."));
    noise->addLayout(noiseRow);

    QFrame *outputBox; auto *destination = card(body, t("Output"), &outputBox); right->addWidget(outputBox);
    output_ = new QLineEdit(outputBox); output_->setObjectName("outputFolder"); output_->setPlaceholderText(t("Destination folder"));
    chooseOutput_ = new QPushButton(t("Choose…"), outputBox);
    auto *outputRow = new QHBoxLayout; outputRow->addWidget(output_, 1); outputRow->addWidget(chooseOutput_); destination->addLayout(outputRow);
    // Un BRAW por toma con el nombre de la toma y los ajustes; si ya existe, la toma se salta (nunca se sobrescribe).
    // El WAV de cada toma se incluye solo si es único y a 48 kHz.
    auto *naming = label(QString(), outputBox, "muted"); naming->setObjectName("outputNaming"); destination->addWidget(naming);

    QFrame *advancedBox; auto *advanced = card(body, t("Advanced"), &advancedBox); right->addWidget(advancedBox, 1);
    auto *workerRow = new QHBoxLayout;
    workers_ = new QSpinBox(advancedBox); workers_->setObjectName("workers"); workers_->setRange(1, qBound(1, QThread::idealThreadCount(), 32)); workers_->setValue(qBound(1, QThread::idealThreadCount() / 2, 32));
    workerRow->addWidget(label(t("Workers"), advancedBox)); workerRow->addWidget(workers_);
    workerRow->addWidget(label(t("frames in parallel · about 0.5 GB each in UHD"), advancedBox, "muted"), 1);
    advanced->addLayout(workerRow);
    // Giroscopio del módulo Gyro2 de fpSup (Sigma fp): dentro del BRAW o en ficheros aparte (.gcsv y .fsg2), y el
    // proyecto con el perfil del objetivo. El plugin usa el BRAW y el proyecto.
    auto *gyroRow = new QHBoxLayout;
    gyroBraw_ = new QCheckBox(t("Inside BRAW file"), advancedBox); gyroBraw_->setObjectName("gyroBraw"); gyroBraw_->setChecked(true);
    gyroBraw_->setToolTip(t("Motion tracks as a Blackmagic camera writes them: Gyroflow and its plugin read them when they open the BRAW."));
    gyroFiles_ = new QCheckBox(t("In separate files (.gcsv/.fsg2)"), advancedBox); gyroFiles_->setObjectName("gyroFiles");
    gyroFiles_->setToolTip(t(".gcsv (Gyroflow's standard motion log) and .fsg2 (the camera's FSG2 blocks and lens data, frame by frame) next to the BRAW, in case it is transcoded or to check them."));
    gyroProject_ = new QCheckBox(t(".gyroflow project"), advancedBox); gyroProject_->setObjectName("gyroProject"); gyroProject_->setChecked(true);
    gyroProject_->setToolTip(t("Gyroflow project next to the BRAW with the lens profile calculated from the DNGs: the plugin loads it by itself."));
    for (auto *box : {gyroBraw_, gyroFiles_, gyroProject_}) box->setToolTip(box->toolTip() + "\n" + t("Only for takes recorded with Gyro2 (fpSup); other takes produce no gyro files."));
    gyroRow->setSpacing(10); gyroRow->addWidget(label(t("Gyro"), advancedBox));
    for (auto *box : {gyroBraw_, gyroFiles_, gyroProject_}) { gyroRow->addWidget(box); gyroRow->addSpacing(8); }
    gyroRow->addStretch();
    advanced->addLayout(gyroRow);
    // Síntesis de ruido del descodificador: el BRAW indica en cada fotograma cuánto ruido reponer donde la compresión
    // lo ha quitado. Sin ella, la salida más fiel al original.
    auto *decoderNoiseRow = new QHBoxLayout;
    noiseSynthesis_ = new QCheckBox(t("Restore the noise removed by compression"), advancedBox); noiseSynthesis_->setObjectName("noiseSynthesis"); noiseSynthesis_->setChecked(true);
    noiseSynthesis_->setToolTip(t("Each frame tells the decoder how much noise to add back where the compression removed it, as the camera does; it hides banding and blocks when grading. Off: the decoded image is closest to the original, with no added noise."));
    decoderNoiseRow->addWidget(label(t("Decoder noise"), advancedBox)); decoderNoiseRow->addWidget(noiseSynthesis_); decoderNoiseRow->addStretch();
    advanced->addLayout(decoderNoiseRow);
    // Pocket OG: viewing DWG/Intermediate (entrada de la LUT medida). Default desactivado = P3-D65 lineal histórico.
    matchCinemaDngRow_ = new QWidget(advancedBox);
    auto *matchRow = new QHBoxLayout(matchCinemaDngRow_);
    matchRow->setContentsMargins(0, 0, 0, 0);
    matchCinemaDng_ = new QCheckBox(t("Match original CinemaDNG rendering"), matchCinemaDngRow_);
    matchCinemaDng_->setObjectName("matchCinemaDng"); matchCinemaDng_->setChecked(false);
    matchCinemaDng_->setToolTip(t("Pocket Cinema Camera (2013) only. Opens in DaVinci Wide Gamut / DaVinci Intermediate with colour science Gen 5. Apply the LUT in the cdng2braw folder for the DNG's Blackmagic Design look. Off: P3-D65 linear, as previous conversions."));
    matchRow->addWidget(label(t("CinemaDNG look"), matchCinemaDngRow_)); matchRow->addWidget(matchCinemaDng_); matchRow->addStretch();
    matchCinemaDngRow_->setVisible(false);
    advanced->addWidget(matchCinemaDngRow_);
    advanced->addStretch();
    // El gestor del perfil de la Pocket original, en el botón «Perfil de cámara…» de la cabecera.
    // Con tomas de la Pocket en el lote, la fila de estado del origen ya ofrece instalarlo.
    const auto openPocketProfile = [this] {
        if (auto *window = profileWindow("pocket-og")) { window->refresh(); window->show(); window->raise(); }
    };
    cameraProfiles->setToolTip(t("The original Pocket Cinema Camera has no Blackmagic RAW profile in Resolve: this app installs its own Camera Support calibration."));
    connect(cameraProfiles, &QPushButton::clicked, this, openPocketProfile);
    advancedCard_ = advancedBox;

    auto *footer = new QFrame(this); footer->setObjectName("footer"); auto *footerLayout = new QVBoxLayout(footer); footerLayout->setContentsMargins(14, 10, 14, 12); footerLayout->setSpacing(8);
    progress_ = new QProgressBar(footer); progress_->setRange(0, 100); progress_->setValue(0); progress_->setTextVisible(false); footerLayout->addWidget(progress_);
    details_ = new QPlainTextEdit(footer); details_->setReadOnly(true); details_->setMaximumBlockCount(120); details_->setMaximumHeight(65); details_->hide(); footerLayout->addWidget(details_);
    resolveHint_ = label(t("Pocket Cinema Camera (2013) takes open in Resolve in P3-D65 linear, where they match the DNG. For the DNG's look, decode them in DaVinci Wide Gamut / DaVinci Intermediate and apply the LUT in the cdng2braw folder (details in the camera profile)."), footer);
    resolveHint_->setObjectName("resolveHint"); resolveHint_->setWordWrap(true); resolveHint_->hide(); footerLayout->addWidget(resolveHint_);
    auto *buttons = new QHBoxLayout;
    state_ = label(t("Ready. Choose a folder with CinemaDNG takes."), footer); state_->setObjectName("conversionState"); buttons->addWidget(state_, 1);
    reveal_ = new QPushButton(t("Open folder"), footer); reveal_->setEnabled(false);
    cancel_ = new QPushButton(t("Cancel"), footer); cancel_->setObjectName("cancelConversion"); cancel_->setEnabled(false);
    convert_ = new QPushButton(t("Convert"), footer); convert_->setObjectName("startConversion"); convert_->setDefault(true);
    buttons->addWidget(reveal_); buttons->addWidget(cancel_); buttons->addWidget(convert_); footerLayout->addLayout(buttons);
    // Crédito abajo a la derecha, separado de los botones.
    auto *footerCredit = label(creditText, footer, "muted"); footerCredit->setObjectName("footerCredit");
    footerCredit->setAlignment(Qt::AlignRight); footerCredit->setOpenExternalLinks(true);
    footerLayout->addSpacing(10); footerLayout->addWidget(footerCredit);
    layout->addWidget(footer);

    profiles_ = new ProfileWindow(store_, this); profiles_->setWindowFlag(Qt::Window, true);
    job_ = new ConversionJob(std::move(backend), this);
    previewService_ = new PreviewService(job_->backendPath(), this);
    previewTimer_ = new QTimer(this); previewTimer_->setSingleShot(true); previewTimer_->setInterval(140);
    connect(previewTimer_, &QTimer::timeout, this, [this] {
        if (!job_->busy() && !clipFolder_.isEmpty()) previewService_->request(clipFolder_, frame_->value(), selectedSize());
    });
    connect(frame_, &QSlider::valueChanged, this, [this](int index) {
        frameNumber_->setText(QString("%1 / %2").arg(index + 1).arg(frame_->maximum() + 1));
        previewState_->setText(t("Developing frame…")); previewTimer_->start();
    });
    connect(input_, &QLineEdit::editingFinished, this, &ConverterWindow::loadSource);
    connect(language_, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] {
        if (job_->busy() || anyProfileOperation()) {
            QSignalBlocker block(language_); language_->setCurrentIndex(language_->findData(languagePreference()));
            QMessageBox::information(this, t("Operation in progress"), t("Finish the current operation before changing language.")); return;
        }
        emit languageChangeRequested(language_->currentData().toString());
    });
    connect(resolution_, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] { updateResolution(); });
    for (auto *spin : {outputWidth_, outputHeight_}) connect(spin, qOverload<int>(&QSpinBox::valueChanged), this, [this] { updateResolution(); });
    connect(compareNoise_, &QPushButton::clicked, this, &ConverterWindow::openNoiseDialog);
    connect(chooseInput_, &QPushButton::clicked, this, [this] {
        const auto path = QFileDialog::getExistingDirectory(this, t("Select a folder with CinemaDNG takes"), input_->text());
        if (path.isEmpty()) return;
        input_->setText(path); loadedRoot_.clear(); loadSource();
    });
    connect(chooseOutput_, &QPushButton::clicked, this, [this] {
        const auto path = QFileDialog::getExistingDirectory(this, t("Destination folder"), output_->text());
        if (!path.isEmpty()) { output_->setText(path); updateBatchControls(); }
    });
    connect(output_, &QLineEdit::textChanged, this, [this] { updateBatchControls(); });
    connect(clips_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto items = clips_->selectedItems();
        if (!items.isEmpty() && !job_->busy()) selectClip(scanned_.at(items.first()->data(0, Qt::UserRole).toInt()).toObject().value("path").toString());
    });
    connect(clips_, &QTreeWidget::itemChanged, this, [this] { updateBatchControls(); updateCompression(); refreshProfile(); updateMatchCinemaDng(); });
    connect(matchCinemaDng_, &QCheckBox::toggled, this, [this] { updateCompression(); });
    for (auto *button : {selectAll_, selectNone_}) {
        const bool check = button == selectAll_;
        connect(button, &QPushButton::clicked, this, [this, check] { setAllChecked(check); });
    }
    connect(previewService_, &PreviewService::scanReady, this, [this](const QJsonObject &result) {
        scanned_ = result.value("clips").toArray(); showClips();
    });
    connect(previewService_, &PreviewService::scanProgress, this, [this](const QJsonObject &clip, int index, int total) {
        // La lista crece según el motor encuentra tomas; la selección y la vista previa esperan al final.
        scanned_.append(clip); showClips(false);
        sourceCompatibility_->setText(QString(t("Looking for takes… %1 of %2")).arg(scanned_.size()).arg(total));
    });
    connect(ratioMode_, &QRadioButton::toggled, this, &ConverterWindow::updateCompression);
    connect(qualityMode_, &QRadioButton::toggled, this, &ConverterWindow::updateCompression);
    connect(qualities_, &QButtonGroup::idClicked, this, &ConverterWindow::updateCompression);
    connect(qCustomCheck_, &QCheckBox::toggled, this, [this](bool on) {
        // Al activar la q libre arranca en la q del nivel marcado, para no dar un salto al convertir.
        if (on) { QSignalBlocker block(qCustom_); qCustom_->setCurrentIndex(qualities_->checkedId()); }
        updateCompression();
    });
    connect(qCustom_, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] { updateCompression(); });
    for (auto *button : ratios_->buttons()) connect(button, &QAbstractButton::clicked, this, &ConverterWindow::updateCompression);
    for (auto *button : nrPresets_->buttons()) connect(button, &QAbstractButton::clicked, this, [this] {
        const auto preset = nrPresets_->checkedId();
        if (preset < 4) {
            QSignalBlocker blockLuma(nrLuma_), blockChroma(nrChroma_);
            nrLuma_->setValue(NoisePresets[preset][0]); nrChroma_->setValue(NoisePresets[preset][1]);
        }
        updateNoiseControls();
    });
    connect(profileButton_, &QPushButton::clicked, this, [this] {
        auto *window = profileWindow(requiredProfile_); if (!window) window = profiles_;
        window->refresh(); window->show(); window->raise();
    });
    connect(convert_, &QPushButton::clicked, this, &ConverterWindow::start);
    connect(cancel_, &QPushButton::clicked, job_, &ConversionJob::cancel);
    connect(reveal_, &QPushButton::clicked, this, [this] { QDesktopServices::openUrl(QUrl::fromLocalFile(output_->text())); });
    connect(previewService_, &PreviewService::ready, this, [this](const QJsonObject &info, const QImage &image) {
        sourceInfo_ = info; const int index = info.value("frame").toInt(), total = info.value("total").toInt();
        { QSignalBlocker block(frame_); frame_->setRange(0, total - 1); frame_->setValue(index); }
        frame_->setEnabled(!job_->busy()); frameNumber_->setText(QString("%1 / %2").arg(index + 1).arg(total));
        const auto width = info.value("width").toInt(), height = info.value("height").toInt();
        preview_->setImage(image, QString("f %1 · %2×%3").arg(index + 1).arg(info.value("preview_width").toInt(width)).arg(info.value("preview_height").toInt(height)));
        previewState_->setText(t("Approximate DNG preview · capture WB, exposure and crop."));
        const auto exposure = info.value("exposure").toDouble();
        const auto shutter = exposure > 0 && exposure < 1 ? QString("1/%1 s").arg(1 / exposure, 0, 'f', 0) : exposure > 0 ? QString("%1 s").arg(exposure, 0, 'g', 3) : QString("—");
        const auto route = info.value("route").toString();
        // Solo los datos que traiga el DNG (la Pocket original no guarda ISO ni obturación).
        QStringList details{QString("%1×%2").arg(width).arg(height), QString("%1 fps").arg(info.value("fps").toDouble(), 0, 'g', 5)};
        if (info.value("iso").toInt() > 0) details << QString("ISO %1").arg(info.value("iso").toInt());
        if (exposure > 0) details << shutter;
        details << QString("%1 EV").arg(info.value("baseline_ev").toDouble(), 0, 'g', 3);
        details << (info.value("kelvin").isNull() ? t("WB 5600 K / tint 10") : QString(t("≈ %1 K · tint %2")).arg(info.value("kelvin").toInt()).arg(info.value("tint").toInt()));
        details << info.value("audio").toString().section(" · ", 0, 1);
        clipDetails_->setText(details.join(" · "));
        clipDetails_->setToolTip(route == "va" ? t("Sigma fp clips reproduce the Blackmagic Video Assist recording (measured on a real sample) and open in any Resolve without installing a profile.")
                                 : route == "og" ? t("The original Pocket Cinema Camera has no Blackmagic RAW profile in Resolve: this app installs its own Camera Support calibration. The BRAW opens in P3-D65 linear, where it matches the DNG.")
                                 : t("Other CinemaDNG cameras are developed through their DNG matrices and carry the BMPCC 4K identity."));
        if (!info.value("compatible").toBool()) previewState_->setText(info.value("compatibility").toString());
        updateResolution(false);
        refreshProfile();
        // The camera needs our own Camera Support profile: ask once per batch folder if it is not installed.
        if (!requiredProfile_.isEmpty() && !profileReady() && offeredFor_ != input_->text() && !job_->busy()) {
            offeredFor_ = input_->text();
            QTimer::singleShot(0, this, [this] { if (auto *w = profileWindow(requiredProfile_)) { w->offerInstall(); refreshProfile(); } });
        }
    });
    connect(previewService_, &PreviewService::error, this, [this](const QString &text) {
        previewState_->setText(text);
        if (scanned_.isEmpty()) { sourceCompatibility_->setText(text); sourceCompatibility_->setStyleSheet("color:#bd554c;"); }
    });
    connect(job_, &ConversionJob::message, this, [this](const QString &text) { state_->setText(text); });
    connect(job_, &ConversionJob::progress, this, [this](int done, int total) {
        const double fraction = total ? double(done) / total : 0;
        if (auto *item = clips_->topLevelItem(current_)) item->setText(4, QString(t("Converting %1 %")).arg(int(fraction * 100)));
        showBatchProgress(fraction);
    });
    connect(workers_, qOverload<int>(&QSpinBox::valueChanged), job_, &ConversionJob::setWorkers);
    connect(job_, &ConversionJob::finished, this, [this](bool ok, bool cancelled, const QString &detail) {
        if (!cancelled) doneWork_ += clipWork(current_);   // también las que fallan: ya no quedan por hacer
        auto *item = clips_->topLevelItem(current_);
        const auto name = scanned_.at(current_).toObject().value("name").toString();
        if (ok) {
            ++converted_;
            if (scanned_.at(current_).toObject().value("route").toString() == "og") ++convertedOg_;
            const int replaced = job_->replacedFrames().size();
            item->setText(4, replaced ? QString(t("Converted · %1 black")).arg(replaced) : t("Converted"));
            batchLog_ << name + ": " + detail.section('\n', 2).replace('\n', " · ");
        }
        else if (cancelled) { batchCancelled_ = true; item->setText(4, t("Cancelled")); }
        else { ++failed_; item->setText(4, t("Error")); item->setToolTip(4, detail); batchLog_ << name + ": " + detail.section('\n', 0, 0); }
        if (cancelled) finishBatch(); else startNext();
    });
    updateResolution(false); updateNoiseControls(); updateMatchCinemaDng(); refreshProfile();
}
void ConverterWindow::loadSource() {
    // Al desactivar los controles al empezar un lote, el campo pierde el foco y emite editingFinished: sin esta
    // guarda se vaciaba la lista justo antes de convertir. El campo tampoco reexplora si la carpeta no ha cambiado.
    if (job_->busy() || batching_ || (input_->text() == loadedRoot_ && !scanned_.isEmpty())) return;
    loadedRoot_ = input_->text();
    previewTimer_->stop(); previewService_->stop(); sourceInfo_ = {}; scanned_ = {}; clipFolder_.clear();
    { QSignalBlocker block(clips_); clips_->clear(); }
    details_->clear(); details_->hide(); progress_->setValue(0);   // el resumen del lote anterior no es de esta carpeta
    clipDetails_->setText("—"); frame_->setEnabled(false); frameNumber_->setText("— / —");
    preview_->clear(t("Looking for takes…")); previewState_->setText(t("Looking for CinemaDNG takes in the folder and its subfolders…"));
    sourceCompatibility_->setStyleSheet({}); sourceCompatibility_->setText(t("Looking for takes…"));
    updateBatchControls(); updateCompression();
    previewService_->requestScan(input_->text());
}
void ConverterWindow::showClips(bool final) {
    QSignalBlocker block(clips_);
    clips_->clear();
    int usable = 0, others = 0; long long frames = 0; double seconds = 0;
    for (int i = 0; i < scanned_.size(); ++i) {
        const auto clip = scanned_.at(i).toObject();
        const bool ok = clip.value("compatible").toBool();
        auto *item = new QTreeWidgetItem(clips_);
        item->setData(0, Qt::UserRole, i);
        item->setText(0, clip.value("name").toString());
        auto camera = clip.value("camera").toString();
        if (camera == "Blackmagic Pocket Cinema Camera") camera = "Pocket (2013)";
        // Cómo se convierte cada toma, con una marca y su explicación: la Pocket original, con el perfil propio; otras
        // cámaras, por la ruta genérica con el balance fijado. La Sigma fp no lleva marca: es el caso normal.
        const auto route = clip.value("route").toString();
        if (route == "og" || route == "pocket") {
            item->setText(1, camera + " ⓘ");
            item->setToolTip(1, route == "og"
                ? t("Converted with this app's own profile for the original Pocket Cinema Camera. Resolve needs the profile installed on every computer that opens the BRAW. It opens in P3-D65 linear, where it matches the DNG; for the DNG's Blackmagic Design look, decode it in DaVinci Wide Gamut / Intermediate and apply the LUT installed with the profile.")
                : t("Camera without its own calibration: the colour comes from the DNG's matrices and the white balance stays as shot. In Resolve it can be corrected, but not set from scratch as with a raw clip."));
        } else item->setText(1, camera);
        if (ok) {
            const auto summary = QString("%1 frames · %2×%3 · %4 fps · %5").arg(clip.value("frames").toInt())
                .arg(clip.value("width").toInt()).arg(clip.value("height").toInt()).arg(clip.value("fps").toDouble(), 0, 'g', 5)
                .arg(clip.value("audio").toString());
            item->setText(2, duration(clip.value("duration").toDouble()));
            const auto out = sizeForClip(clip);
            item->setText(3, out.width() > 0 && out.height() > 0 ? QString("%1×%2").arg(out.width()).arg(out.height()) : QString("—"));
            const int missing = clip.value("missing").toInt();
            item->setText(4, missing ? QString(t("Ready · %1 missing")).arg(missing) : t("Ready"));
            for (int column = 0; column < 5; ++column) if (column != 1 || item->toolTip(1).isEmpty()) item->setToolTip(column, summary);
            if (route == "pocket") ++others;
            item->setCheckState(0, Qt::Checked);
            ++usable; frames += clip.value("frames").toInt(); seconds += clip.value("duration").toDouble();
        } else {
            item->setText(4, t("Not supported"));
            for (int column = 0; column < 5; ++column) item->setToolTip(column, clip.value("compatibility").toString());
            item->setFlags(item->flags() & ~Qt::ItemIsUserCheckable); item->setForeground(4, QColor("#bd554c"));
        }
    }
    for (int column = 0; column < clips_->columnCount(); ++column) clips_->resizeColumnToContents(column);
    if (!final) return;
    sourceCompatibility_->setText(scanned_.isEmpty() ? t("No CinemaDNG takes in this folder.")
        : QString(t("%1 · %2 · %3 frames · %4"))
              .arg(scanned_.size() == 1 ? t("1 take found") : QString(t("%1 takes found")).arg(scanned_.size()))
              .arg(usable == 1 ? t("1 can be converted") : QString(t("%1 can be converted")).arg(usable))
              .arg(frames).arg(duration(seconds))
        + (others ? " · " + (others == 1 ? t("1 from another camera, with the white balance fixed")
                                          : QString(t("%1 from other cameras, with the white balance fixed")).arg(others)) : QString()));
    sourceCompatibility_->setStyleSheet(usable ? QString() : "color:#bd554c;");
    if (!job_->busy())
        state_->setText(scanned_.isEmpty() ? t("Ready. Choose a folder with CinemaDNG takes.")
                        : usable ? t("Ready. Check the takes and press Convert.") : t("No take in this folder can be converted."));
    if (output_->text().isEmpty() && !input_->text().isEmpty()) output_->setText(QDir(input_->text()).filePath("BRAW"));
    updateBatchControls(); updateCompression(); refreshProfile(); updateMatchCinemaDng();
    // La primera toma convertible queda seleccionada para la vista previa.
    for (int i = 0; i < clips_->topLevelItemCount(); ++i)
        if (scanned_.at(i).toObject().value("compatible").toBool()) { clips_->setCurrentItem(clips_->topLevelItem(i)); selectClip(scanned_.at(i).toObject().value("path").toString()); return; }
    // Ninguna se puede convertir: se muestra la primera igualmente, para poder inspeccionarla.
    if (clips_->topLevelItemCount()) { clips_->setCurrentItem(clips_->topLevelItem(0)); selectClip(scanned_.at(0).toObject().value("path").toString()); return; }
    preview_->clear(t("No take to preview"));
}
void ConverterWindow::selectClip(const QString &folder) {
    if (folder == clipFolder_ && !sourceInfo_.isEmpty()) return;
    clipFolder_ = folder; sourceInfo_ = {};
    previewTimer_->stop(); frame_->setEnabled(false); frameNumber_->setText("— / —"); clipDetails_->setText("—");
    preview_->clear(t("Loading preview…")); previewState_->setText(t("Reading metadata and developing the first frame…"));
    previewService_->request(clipFolder_, 0, selectedSize());
}
void ConverterWindow::setAllChecked(bool checked) {
    if (job_->busy()) return;   // durante un lote, la cola ya está decidida
    // Un solo repintado y una sola actualización: itemChanged salta por cada fila.
    const QSignalBlocker block(clips_);
    for (int i = 0; i < clips_->topLevelItemCount(); ++i) {
        auto *item = clips_->topLevelItem(i);
        if (item->flags() & Qt::ItemIsUserCheckable) item->setCheckState(0, checked ? Qt::Checked : Qt::Unchecked);
    }
    updateBatchControls(); updateCompression(); refreshProfile(); updateMatchCinemaDng();
}
QList<int> ConverterWindow::checkedClips() const {
    QList<int> out;
    for (int i = 0; i < clips_->topLevelItemCount(); ++i)
        if (clips_->topLevelItem(i)->checkState(0) == Qt::Checked) out << clips_->topLevelItem(i)->data(0, Qt::UserRole).toInt();
    return out;
}
void ConverterWindow::updateBatchControls() {
    const auto checked = checkedClips();
    convert_->setText(checked.size() == 1 ? t("Convert 1 take") : QString(t("Convert %1 takes")).arg(checked.size()));
    if (!job_->busy()) convert_->setEnabled(!checked.isEmpty() && !output_->text().isEmpty());
    if (auto *naming = findChild<QLabel *>("outputNaming")) {
        const auto example = checked.isEmpty() ? QString() : QFileInfo(outputPathFor(scanned_.at(checked.first()).toObject())).fileName();
        naming->setText(example.isEmpty() ? t("One BRAW per take, named after the take. Existing files are skipped, never overwritten.")
                                          : QString(t("One BRAW per take: %1 … · existing files are skipped")).arg(example));
    }
}
// q efectiva de la calidad fija: la del desplegable si está la q libre, si no la del nivel marcado.
int ConverterWindow::selectedQuality() const {
    return qCustomCheck_->isChecked() ? qCustom_->currentData().toInt() : qualities_->checkedId();
}
void ConverterWindow::updateCompression() {
    const bool ratio = ratioMode_->isChecked();
    const bool freeQ = qCustomCheck_->isChecked();
    for (auto *button : ratios_->buttons()) button->setEnabled(ratio);
    // Con la q libre marcada, el desplegable ocupa el sitio de los niveles; todo ello, activo solo en calidad fija.
    for (auto *button : qualities_->buttons()) button->setEnabled(!ratio);
    qualityButtons_->setVisible(!freeQ); qCustom_->setVisible(freeQ);
    qCustomCheck_->setEnabled(!ratio); qCustom_->setEnabled(!ratio);
    const auto checked = checkedClips();
    if (ratio && !checked.isEmpty()) {
        // Total del lote: cada toma con su tamaño de salida (el suyo propio con «Original»).
        double bytes = 0;
        for (int i : checked) {
            const auto clip = scanned_.at(i).toObject();
            const auto size = sizeForClip(clip);   // cada toma con su proporción, también en los niveles
            bytes += double(size.width()) * size.height() * 12 / 8 / ratios_->checkedId() * clip.value("frames").toInt();
        }
        estimate_->setText(checked.size() == 1 ? QString("≈ %1 MB").arg(bytes / 1e6, 0, 'f', 1)
                                               : QString(t("≈ %1 GB · %2 takes")).arg(bytes / 1e9, 0, 'f', 2).arg(checked.size()));
    } else estimate_->setText(ratio ? t("Select takes to estimate the size.") : t("Fixed quality · size depends on content."));
    updateBatchControls();
}void ConverterWindow::updateNoiseControls() {
    const bool custom = nrPresets_->checkedId() == 4;
    for (auto *box : {nrLuma_, nrChroma_}) box->setEnabled(custom);
}
bool ConverterWindow::scannedHasOg() const {
    for (const auto &value : scanned_)
        if (value.toObject().value("route").toString() == "og") return true;
    return false;
}
void ConverterWindow::updateMatchCinemaDng() {
    const bool og = scannedHasOg();
    matchCinemaDngRow_->setVisible(og);
    matchCinemaDng_->setEnabled(og && !job_->busy());
    if (!og) matchCinemaDng_->setChecked(false);
}
QString ConverterWindow::outputPathFor(const QJsonObject &clip) const {
    // Sin fecha: así un lote interrumpido se reanuda saltando lo que ya existe. Resolve guarda en caché los metadatos
    // por ruta (I8): para repetir una toma con otros ajustes basta con que el nombre lleve los ajustes.
    auto name = clip.value("name").toString();
    if (resolution_->currentData() != "original") { const auto size = sizeForClip(clip); name += QString("_%1x%2").arg(size.width()).arg(size.height()); }
    name += ratioMode_->isChecked() ? QString("_%1to1").arg(ratios_->checkedId()) : QString("_%1").arg(qualityTag(selectedQuality()));
    if (nrLuma_->value() || nrChroma_->value()) name += QString("_nr%1-%2").arg(nrLuma_->value()).arg(nrChroma_->value());
    if (matchCinemaDng_->isChecked() && clip.value("route").toString() == "og") name += "_match";
    return QDir(output_->text()).filePath(name + ".braw");
}ProfileWindow *ConverterWindow::profileWindow(const QString &id) {
    if (id.isEmpty() || ProfileSpec::byId(id).id.isEmpty()) return nullptr;
    if (!cameraProfiles_.contains(id)) {
        auto *window = new ProfileWindow(ProfileStore(store_.paths(), Bundle::embedded(ProfileSpec::byId(id))), this);
        window->setWindowFlag(Qt::Window, true); window->setObjectName("profileWindow-" + id);
        cameraProfiles_.insert(id, window);
    }
    return cameraProfiles_.value(id);
}
bool ConverterWindow::profileReady() {
    if (requiredProfile_.isEmpty()) return true;
    const auto spec = ProfileSpec::byId(requiredProfile_);
    return !spec.id.isEmpty() && ProfileStore(store_.paths(), Bundle::embedded(spec)).inspect().state == ProfileState::Ready;
}
bool ConverterWindow::anyProfileOperation() const {
    if (profiles_->operationInProgress()) return true;
    for (auto *window : cameraProfiles_) if (window->operationInProgress()) return true;
    return false;
}
void ConverterWindow::refreshProfile() {
    // El perfil propio que exige alguna toma marcada del lote (hoy, la Pocket Cinema Camera original).
    requiredProfile_.clear();
    for (int i : checkedClips()) {
        const auto profile = scanned_.at(i).toObject().value("profile").toString();
        if (!profile.isEmpty()) { requiredProfile_ = profile; break; }
    }
    profileRow_->setVisible(!requiredProfile_.isEmpty());
    if (requiredProfile_.isEmpty()) return;
    const auto spec = ProfileSpec::byId(requiredProfile_);
    const bool ready = profileReady();
    const bool update = !ready && ProfileStore(store_.paths(), Bundle::embedded(spec)).inspect().state == ProfileState::Update;
    profileState_->setText(QString(ready ? t("%1: own calibration profile · installed")
                                         : update ? t("%1: own calibration profile · needs updating, required to convert")
                                                  : t("%1: own calibration profile · not installed, required to convert")).arg(spec.displayName));
    profileState_->setStyleSheet(ready ? QString() : "color:#bd554c;");
    profileButton_->setText(ready ? t("Manage profile…") : t("Install profile…"));
}// Resuelve una opción del desplegable contra un tamaño de origen. Los niveles fijan el **ancho** y la altura
// sale de la proporción del material, con el mismo cálculo que hace el motor en outputSize, para que la interfaz
// anuncie exactamente lo que va a salir.
QSize ConverterWindow::sizeForKey(const QString &key, QSize source) const {
    const int sw = source.width(), sh = source.height();
    if (key == "original") return {sw, sh};
    if (key == "custom") return {outputWidth_->value(), outputHeight_->value()};
    const int width = key.toInt();
    if (width <= 0) return {};
    if (sw < 1 || sh < 1) return {width, 0};   // toma sin tamaño conocido todavía: solo se sabe el ancho
    if (width >= sw) return {sw, sh};          // el nivel no amplía: se queda en el original
    return {width, qMin(int(qRound(double(width) * sh / sw / 2) * 2), sh - sh % 2)};
}
// Tamaño de salida de **esta** toma. Va por toma y no por lote porque un lote puede mezclar proporciones
// (16:9 y open gate) y cada una tiene que conservar la suya.
QSize ConverterWindow::sizeForClip(const QJsonObject &clip) const {
    return sizeForKey(resolution_->currentData().toString(),
                      QSize(clip.value("width").toInt(), clip.value("height").toInt()));
}
QSize ConverterWindow::selectedSize() const { return sizeForClip(sourceInfo_); }
void ConverterWindow::updateResolution(bool requestPreview) {
    customSize_->setVisible(resolution_->currentData() == "custom");
    const auto size = selectedSize();
    resolutionSize_->setText(size.width() > 0 && size.height() > 0 ? QString("%1 × %2").arg(size.width()).arg(size.height()) : QString("—"));
    // La columna «Salida» de cada toma depende del nivel elegido: se recalcula aquí, toma a toma.
    for (int i = 0; i < clips_->topLevelItemCount(); ++i) {
        auto *item = clips_->topLevelItem(i);
        const int index = item->data(0, Qt::UserRole).toInt();
        if (index < 0 || index >= scanned_.size()) continue;
        const auto clip = scanned_.at(index).toObject();
        if (!clip.value("compatible").toBool()) continue;
        const auto out = sizeForClip(clip);
        item->setText(3, out.width() > 0 && out.height() > 0 ? QString("%1×%2").arg(out.width()).arg(out.height()) : QString("—"));
    }
    updateCompression();
    if (requestPreview && !sourceInfo_.isEmpty() && !job_->busy()) { previewState_->setText(t("Loading preview…")); previewTimer_->start(); }
}
void ConverterWindow::openNoiseDialog() {
    if (job_->busy() || anyProfileOperation()) return;
    if (sourceInfo_.isEmpty()) { QMessageBox::information(this, t("Compare original and result"), t("Select a sequence before opening the comparison.")); return; }
    if (!sourceInfo_.value("compatible").toBool()) {
        QMessageBox::information(this, t("Compare original and result"), sourceInfo_.value("compatibility").toString()); return;
    }
    previewTimer_->stop(); previewService_->stop();
    ComparisonSettings settings{double(nrLuma_->value()), double(nrChroma_->value()), nrPresets_->checkedId(),
        ratioMode_->isChecked() ? "ratio" : "quality", ratios_->checkedId(), selectedQuality()};
    // Las resoluciones de la ventana (la personalizada, solo si es la elegida), con su tamaño ya resuelto.
    const QSize native(sourceInfo_.value("width").toInt(), sourceInfo_.value("height").toInt());
    for (int i = 0; i < resolution_->count(); ++i) {
        const auto key = resolution_->itemData(i).toString();
        if (key == "custom" && resolution_->currentData() != "custom") continue;
        const QSize size = sizeForKey(key, native);   // mismo cálculo que la ventana
        const auto label = key == "original" ? QString("%1 · %2×%3").arg(t("Original")).arg(size.width()).arg(size.height())
            : key == "custom" ? QString("%1 · %2×%3").arg(t("Custom")).arg(size.width()).arg(size.height())
            // El texto del nivel es «3K · 3072»: se queda el nombre y se le pone el tamaño ya resuelto.
            : QString("%1 · %2×%3").arg(resolution_->itemText(i).section(QChar(0x00B7), 0, 0).trimmed()).arg(size.width()).arg(size.height());
        settings.resolutions.append({key, label, size});
    }
    settings.resolution = resolution_->currentData().toString();
    NoiseDialog dialog(job_->backendPath(), clipFolder_, frame_->value(), frame_->maximum() + 1, selectedSize(), preview_->image(), settings, this);
    if (dialog.exec() == QDialog::Accepted) {
        const auto chosen = dialog.settings();
        if (auto *button = nrPresets_->button(chosen.preset)) button->setChecked(true);
        { QSignalBlocker blockLuma(nrLuma_), blockChroma(nrChroma_); nrLuma_->setValue(int(chosen.luma)); nrChroma_->setValue(int(chosen.chroma)); }
        updateNoiseControls();
        // «Sin compresión» solo existe en el comparador: deja la compresión de la ventana como estaba.
        if (chosen.compression == "ratio") { ratioMode_->setChecked(true); if (auto *b = ratios_->button(chosen.ratio)) b->setChecked(true); }
        else if (chosen.compression == "quality") {
            qualityMode_->setChecked(true);
            // El comparador trabaja con niveles: al volver con una calidad, se vuelve a los niveles (sin q libre).
            if (qualityLevelIndex(chosen.q) >= 0) { qCustomCheck_->setChecked(false); qualities_->button(chosen.q)->setChecked(true); }
            else { qCustomCheck_->setChecked(true); QSignalBlocker block(qCustom_); qCustom_->setCurrentIndex(chosen.q); }
        }
        updateCompression();
        if (chosen.resolution != resolution_->currentData().toString()) resolution_->setCurrentIndex(resolution_->findData(chosen.resolution));
    }
}
QVariantMap ConverterWindow::snapshot() const {
    return {{"input", input_->text()}, {"output", output_->text()},
        {"ratioMode", ratioMode_->isChecked()}, {"ratio", ratios_->checkedId()}, {"q", selectedQuality()},
        {"nrPreset", nrPresets_->checkedId()}, {"nrLuma", nrLuma_->value()}, {"nrChroma", nrChroma_->value()},
        {"resolution", resolution_->currentIndex()}, {"width", outputWidth_->value()}, {"height", outputHeight_->value()},
        {"workers", workers_->value()}, {"gyroBraw", gyroBraw_->isChecked()}, {"gyroFiles", gyroFiles_->isChecked()}, {"gyroProject", gyroProject_->isChecked()}, {"noiseSynthesis", noiseSynthesis_->isChecked()}, {"matchCinemaDng", matchCinemaDng_->isChecked()}, {"geometry", saveGeometry()}, {"details", details_->toPlainText()},
        {"detailsVisible", !details_->isHidden()}, {"state", state_->text()}};
}void ConverterWindow::restore(const QVariantMap &state) {
    input_->setText(state.value("input").toString());
    ratioMode_->setChecked(state.value("ratioMode").toBool()); qualityMode_->setChecked(!ratioMode_->isChecked());
    if (auto *button = ratios_->button(state.value("ratio", 5).toInt())) button->setChecked(true);
    // La q guardada: si es de un nivel, se marca ese botón; si es libre, se activa la q libre con ese valor.
    const int savedQ = state.value("q", 4).toInt();
    if (qualityLevelIndex(savedQ) >= 0) { qCustomCheck_->setChecked(false); qualities_->button(savedQ)->setChecked(true); }
    else { qCustomCheck_->setChecked(true); qCustom_->setCurrentIndex(qBound(0, savedQ, 24)); }
    resolution_->setCurrentIndex(state.value("resolution").toInt()); outputWidth_->setValue(state.value("width", 3072).toInt()); outputHeight_->setValue(state.value("height", 1728).toInt());
    if (auto *button = nrPresets_->button(state.value("nrPreset").toInt())) button->setChecked(true);
    nrLuma_->setValue(state.value("nrLuma").toInt()); nrChroma_->setValue(state.value("nrChroma").toInt());
    workers_->setValue(state.value("workers", 2).toInt());
    // Las claves antiguas del .gcsv y el .fsg2 (activadas por defecto) no se leen: los ficheros aparte ya no lo están.
    gyroBraw_->setChecked(state.value("gyroBraw", true).toBool()); gyroFiles_->setChecked(state.value("gyroFiles", false).toBool());
    gyroProject_->setChecked(state.value("gyroProject", true).toBool());
    noiseSynthesis_->setChecked(state.value("noiseSynthesis", true).toBool());
    matchCinemaDng_->setChecked(state.value("matchCinemaDng", false).toBool());
    output_->setText(state.value("output").toString());
    restoreGeometry(state.value("geometry").toByteArray());
    details_->setPlainText(state.value("details").toString()); details_->setVisible(state.value("detailsVisible").toBool());
    updateNoiseControls();
    updateMatchCinemaDng();
    loadedRoot_.clear();
    if (!input_->text().isEmpty()) loadSource();   // vuelve a explorar la carpeta (rápido: solo lee etiquetas)
}QString ConverterWindow::droppedFolder(const QMimeData *mime) {
    // One local folder, or one DNG file whose folder is then used. Anything else is refused.
    if (!mime || !mime->hasUrls() || mime->urls().size() != 1 || !mime->urls().first().isLocalFile()) return {};
    const QFileInfo info(mime->urls().first().toLocalFile());
    if (info.isDir()) return info.absoluteFilePath();
    if (info.isFile() && info.suffix().compare("dng", Qt::CaseInsensitive) == 0) return info.absolutePath();
    return {};
}
void ConverterWindow::dragEnterEvent(QDragEnterEvent *event) {
    if (!job_->busy() && !droppedFolder(event->mimeData()).isEmpty()) event->acceptProposedAction(); else event->ignore();
}
void ConverterWindow::dragMoveEvent(QDragMoveEvent *event) {
    if (!job_->busy() && !droppedFolder(event->mimeData()).isEmpty()) event->acceptProposedAction(); else event->ignore();
}
void ConverterWindow::dropEvent(QDropEvent *event) {
    const auto folder = droppedFolder(event->mimeData());
    if (job_->busy() || folder.isEmpty()) { event->ignore(); return; }
    event->acceptProposedAction(); input_->setText(folder); loadedRoot_.clear(); loadSource();
}
void ConverterWindow::offerInstall() { profiles_->offerInstall(); refreshProfile(); }
void ConverterWindow::showEvent(QShowEvent *event) { QWidget::showEvent(event); refreshProfile(); }
void ConverterWindow::setBusy(bool busy) {
    // Los procesos se pueden cambiar durante la conversión (el motor los relee); el resto queda fijo.
    for (QWidget *widget : QList<QWidget *>{input_, output_, chooseInput_, chooseOutput_, convert_, profileButton_, gyroBraw_, gyroFiles_, gyroProject_, noiseSynthesis_, matchCinemaDng_, compressionCard_, nrCard_, resolution_, customSize_, language_, clips_, compareNoise_}) widget->setEnabled(!busy);
    frame_->setEnabled(!busy && !sourceInfo_.isEmpty()); cancel_->setEnabled(busy);
    if (busy) { reveal_->setEnabled(false); details_->hide(); progress_->setRange(0, 0); }
    else { updateNoiseControls(); updateMatchCinemaDng(); }
}
void ConverterWindow::start() {
    if (anyProfileOperation() || job_->busy()) return;
    refreshProfile();
    if (!requiredProfile_.isEmpty() && !profileReady()) {
        if (auto *window = profileWindow(requiredProfile_)) window->offerInstall();
        refreshProfile();
        if (!profileReady()) { state_->setText(t("Install the camera profile before converting.")); return; }
    }
    queue_ = checkedClips();
    if (queue_.isEmpty()) return;
    if (!QDir().mkpath(output_->text())) { state_->setText(t("Could not create the destination folder.")); return; }
    profiles_->hide(); previewTimer_->stop(); previewService_->stop();
    batchSize_ = queue_.size(); converted_ = skipped_ = failed_ = convertedOg_ = 0; batchCancelled_ = false; batchLog_.clear(); resolveHint_->hide(); batchTimer_.start();
    batchWork_ = doneWork_ = 0; workSamples_.clear();
    for (int i : queue_) if (!QFileInfo::exists(outputPathFor(scanned_.at(i).toObject()))) batchWork_ += clipWork(i);
    batching_ = true;
    for (int i : queue_) clips_->topLevelItem(i)->setText(4, t("Queued"));
    setBusy(true);
    startNext();
}
void ConverterWindow::startNext() {
    while (!queue_.isEmpty()) {
        current_ = queue_.takeFirst();
        const auto clip = scanned_.at(current_).toObject();
        auto *item = clips_->topLevelItem(current_);
        const auto output = outputPathFor(clip);
        if (QFileInfo::exists(output)) {   // nunca se sobrescribe: un lote repetido o interrumpido salta lo hecho
            ++skipped_; item->setText(4, t("Already exists · skipped")); item->setToolTip(4, output); continue;
        }
        ConversionOptions options;
        options.input = clip.value("path").toString(); options.output = output; options.q = selectedQuality();
        options.workers = workers_->value(); options.audio = clip.value("audio_include").toBool();
        options.gyroGcsv = options.gyroFsg2 = gyroFiles_->isChecked();
        options.gyroBraw = gyroBraw_->isChecked(); options.gyroProject = gyroProject_->isChecked();
        options.noiseSynthesis = noiseSynthesis_->isChecked();
        options.matchCinemaDngRendering = matchCinemaDng_->isChecked() && clip.value("route").toString() == "og";
        options.compression = ratioMode_->isChecked() ? "ratio" : "quality"; options.ratio = ratios_->checkedId();
        options.nrLuma = nrLuma_->value(); options.nrChroma = nrChroma_->value();
        options.size = resolution_->currentData() == "original" ? QSize() : sizeForClip(clip);
        QString error;
        if (!job_->start(options, &error)) { ++failed_; item->setText(4, t("Error")); item->setToolTip(4, error); batchLog_ << clip.value("name").toString() + ": " + error; continue; }
        item->setText(4, t("Converting…")); clips_->scrollToItem(item);
        state_->setText(QString(t("Take %1 of %2 · %3 · preparing…")).arg(batchSize_ - queue_.size()).arg(batchSize_).arg(clip.value("name").toString()));
        return;
    }
    finishBatch();
}
double ConverterWindow::clipWork(int index) const {
    const auto clip = scanned_.at(index).toObject();
    return clip.value("frames").toDouble() * clip.value("width").toDouble() * clip.value("height").toDouble();
}
void ConverterWindow::showBatchProgress(double fraction) {
    // Barra y estado del lote: trabajo hecho (tomas terminadas + fracción de la actual) y lo que queda al ritmo medido.
    const double work = doneWork_ + clipWork(current_) * fraction, share = batchWork_ > 0 ? qMin(1., work / batchWork_) : 0;
    progress_->setRange(0, 1000); progress_->setValue(int(1000 * share));
    const int position = batchSize_ - queue_.size();
    QString text = QString(t("Take %1 of %2 · %3 · %4 %")).arg(position).arg(batchSize_)
        .arg(scanned_.at(current_).toObject().value("name").toString()).arg(int(fraction * 100));
    // Ritmo del último minuto (sigue a un cambio de procesos o de tipo de toma); al principio, el medio del lote.
    const double seconds = batchTimer_.elapsed() / 1000.;
    workSamples_.append({seconds, work});
    while (workSamples_.size() > 2 && seconds - workSamples_.first().x() > 60) workSamples_.removeFirst();
    const auto &oldest = workSamples_.first();
    const double rate = seconds - oldest.x() >= 15 && work > oldest.y() ? (work - oldest.y()) / (seconds - oldest.x()) : work / qMax(seconds, 1.);
    if (work > 0 && seconds > 20 && share < 1) {
        const double left = (batchWork_ - work) / rate;
        const int minutes = int(std::lround(left / 60));
        const auto eta = minutes < 60 ? QString(t("%1 min")).arg(minutes) : QString(t("%1 h %2 min")).arg(minutes / 60).arg(minutes % 60);
        text += " · " + (left < 60 ? QString(t("batch %1 % · less than a minute left")).arg(int(share * 100))
                                   : QString(t("batch %1 % · about %2 left")).arg(int(share * 100)).arg(eta));
    }
    state_->setText(text);
}
void ConverterWindow::finishBatch() {
    for (int i : queue_) clips_->topLevelItem(i)->setText(4, t("Not converted"));
    queue_.clear(); current_ = -1; batching_ = false;
    setBusy(false);
    const auto summary = QString(t("%1 converted · %2 skipped · %3 with errors · %4")).arg(converted_).arg(skipped_).arg(failed_).arg(duration(batchTimer_.elapsed() / 1000.0));
    state_->setText((batchCancelled_ ? t("Batch cancelled: ") : t("Batch finished: ")) + summary);
    progress_->setRange(0, 100); progress_->setValue(batchCancelled_ ? 0 : 100);
    reveal_->setEnabled(converted_ > 0 || skipped_ > 0);
    if (!batchLog_.isEmpty()) { details_->setPlainText(batchLog_.join('\n')); details_->show(); }
    resolveHint_->setText(matchCinemaDng_->isChecked()
        ? t("Pocket Cinema Camera (2013) takes with Match original CinemaDNG rendering open in Resolve in DaVinci Wide Gamut / DaVinci Intermediate. Apply the LUT in the cdng2braw folder for the DNG's Blackmagic Design look (details in the camera profile). This is not automatic Gen 1 equivalence.")
        : t("Pocket Cinema Camera (2013) takes open in Resolve in P3-D65 linear, where they match the DNG. For the DNG's look, decode them in DaVinci Wide Gamut / DaVinci Intermediate and apply the LUT in the cdng2braw folder (details in the camera profile)."));
    resolveHint_->setVisible(convertedOg_ > 0);
    updateBatchControls();
}void ConverterWindow::closeEvent(QCloseEvent *event) {
    if (job_->busy() || anyProfileOperation()) {
        QMessageBox::information(this, t("Operation in progress"), t("Cancel or wait for the operation to finish before closing the application."));
        event->ignore(); return;
    }
    previewService_->stop(); profiles_->close(); for (auto *window : cameraProfiles_) window->close(); QWidget::closeEvent(event);
}
}

namespace cdng {
void ConverterWindow::showAbout() {
    QMessageBox box(this);
    box.setObjectName("aboutDialog");
    box.setWindowTitle(t("About CDNG to BRAW Converter"));
    box.setTextFormat(Qt::RichText);
    box.setText(QString("<h3>CDNG to BRAW Converter %1</h3><p>%2</p><p>%3<br><a href=\"https://www.elcacharrista.com\">www.elcacharrista.com</a></p>")
        .arg(QCoreApplication::applicationVersion().toHtmlEscaped(), t("CinemaDNG to Blackmagic RAW converter for the Sigma fp and the original Pocket Cinema Camera.").toHtmlEscaped(),
             t("By Jose A. Hurtado").toHtmlEscaped()));
    box.setInformativeText(t("Conversion engine in C++ with LibRaw. Blackmagic RAW and DaVinci Resolve are trademarks of Blackmagic Design; SIGMA and fp, of SIGMA Corporation. Not affiliated with either company."));
    // Avisos de terceros (las licencias de LibRaw y Qt piden que estén accesibles): se muestran en la app, no abriendo
    // el fichero; sin el fichero (app del árbol de build) no hay botón.
    // En el paquete de macOS están en Contents/Resources; en el de Windows, junto al ejecutable.
    const QDir appDir(QCoreApplication::applicationDirPath());
    QFile file(appDir.filePath("../Resources/THIRD_PARTY_NOTICES.md"));
    if (!file.exists()) file.setFileName(appDir.filePath("THIRD_PARTY_NOTICES.md"));
    QPushButton *notices = file.exists() ? box.addButton(t("Third-party notices…"), QMessageBox::ActionRole) : nullptr;
    box.addButton(t("Close"), QMessageBox::RejectRole);
    box.exec();
    if (notices && box.clickedButton() == notices && file.open(QIODevice::ReadOnly)) {
        QDialog dialog(this);
        dialog.setObjectName("noticesDialog");
        dialog.setWindowTitle(t("Third-party notices"));
        auto *layout = new QVBoxLayout(&dialog);
        auto *text = new QTextBrowser(&dialog);
        text->setOpenExternalLinks(true);
        text->setMarkdown(QString::fromUtf8(file.readAll()));
        layout->addWidget(text);
        auto *buttons = new QDialogButtonBox(&dialog);
        connect(buttons->addButton(t("Close"), QDialogButtonBox::RejectRole), &QPushButton::clicked, &dialog, &QDialog::reject);
        layout->addWidget(buttons);
        dialog.resize(720, 560);
        dialog.exec();
    }
}
}  // namespace cdng
