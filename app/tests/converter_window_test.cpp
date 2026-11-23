#include "converter_window.h"
#include "i18n.h"
#include "noise_dialog.h"
#include <QTreeWidget>
#include <QRegularExpression>
#include <QFile>
#include <QFileInfo>
#include <QButtonGroup>
#include <QCheckBox>
#include <QComboBox>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QRadioButton>
#include <QDoubleSpinBox>
#include <QSlider>
#include <QSpinBox>
#include <QTemporaryDir>
#include <QMimeData>
#include <QDropEvent>
#include <QDragEnterEvent>
#include <QtTest>

using namespace cdng;
class ConverterWindowTests : public QObject {
    Q_OBJECT
private slots:
    void initTestCase() { initializeLanguage("es"); }
    void languageSwitchPreservesSettings() {
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        initializeLanguage("en");
        ConverterWindow english(store, temp.path() + "/missing-worker");
        QVERIFY(english.findChild<QPushButton *>("startConversion")->text().startsWith("Convert "));
        english.findChild<QLineEdit *>("inputFolder")->setText(temp.path());
        english.findChild<QComboBox *>("outputResolution")->setCurrentIndex(5);
        english.findChild<QSpinBox *>("outputWidth")->setValue(2816);
        english.findChild<QSpinBox *>("outputHeight")->setValue(1584);
        english.findChild<QLineEdit *>("outputFolder")->setText(temp.path() + "/chosen");
        const auto state = english.snapshot();
        initializeLanguage("es");
        ConverterWindow spanish(store, temp.path() + "/missing-worker"); spanish.restore(state);
        QVERIFY(spanish.findChild<QPushButton *>("startConversion")->text().startsWith("Convertir "));
        QCOMPARE(spanish.findChild<QLineEdit *>("outputFolder")->text(), temp.path() + "/chosen");
        QCOMPARE(spanish.findChild<QSpinBox *>("outputWidth")->value(), 2816);
        QCOMPARE(spanish.findChild<QSpinBox *>("outputHeight")->value(), 1584);
    }
    void compressionAndNoiseDefaultsAreSafe() {
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        ConverterWindow window(store, temp.path() + "/missing-worker");
        QVERIFY(window.findChild<QRadioButton *>("ratioMode")->isChecked());
        // Calidad constante en los niveles de Blackmagic.
        for (const auto &[name, q] : QList<QPair<QString, int>>{{"Q0", 0}, {"Q1", 2}, {"Q3", 9}, {"Q5", 20}}) {
            auto *button = window.findChild<QPushButton *>(QString("quality%1").arg(q));
            QVERIFY(button); QCOMPARE(button->text(), name);
            QVERIFY(!button->isEnabled());
        }
        window.findChild<QRadioButton *>("qualityMode")->click();
        QVERIFY(window.findChild<QPushButton *>("quality9")->isEnabled());
        QVERIFY(!window.findChild<QPushButton *>("ratio5")->isEnabled());
        // q libre (0–24): la casilla solo se activa en calidad fija, y al marcarla un desplegable con todas las q ocupa el
        // sitio de los niveles, empezando en la del nivel marcado; las que son un nivel llevan su nombre.
        auto *qCheck = window.findChild<QCheckBox *>("qCustomCheck");
        auto *qList = window.findChild<QComboBox *>("qCustom");
        auto *level9 = window.findChild<QPushButton *>("quality9");
        QVERIFY(qCheck && qList);
        QCOMPARE(qList->count(), 25);
        QCOMPARE(qList->itemText(9), QString("q9 (Q3)")); QCOMPARE(qList->itemText(7), QString("q7"));
        QVERIFY(qCheck->isEnabled()); QVERIFY(qList->isHidden()); QVERIFY(!level9->isHidden());
        level9->click();
        qCheck->click();
        QVERIFY(!qList->isHidden()); QVERIFY(level9->parentWidget()->isHidden());
        QCOMPARE(qList->currentData().toInt(), 9);
        qList->setCurrentIndex(7);
        qCheck->click();
        QVERIFY(qList->isHidden()); QVERIFY(!level9->parentWidget()->isHidden());
        qCheck->click();
        window.findChild<QRadioButton *>("ratioMode")->click();
        QVERIFY(!qCheck->isEnabled() && !qList->isEnabled());                 // fuera de calidad fija, nada de q libre
        QVERIFY(!window.findChild<QComboBox *>("nrTemporal"));   // temporal noise reduction is left to Resolve
        for (const auto *name : {"nrLuma", "nrChroma"}) {
            auto *box = window.findChild<QSpinBox *>(name);
            QCOMPARE(box->value(), 0); QCOMPARE(box->maximum(), 100);
            QVERIFY(!box->isEnabled());
        }
        const QList<QPair<int, int>> presets{{20, 40}, {35, 60}, {50, 80}};
        for (int preset = 1; preset <= 3; ++preset) {
            window.findChild<QPushButton *>(QString("nrPreset%1").arg(preset))->click();
            QCOMPARE(window.findChild<QSpinBox *>("nrLuma")->value(), presets[preset - 1].first);
            QCOMPARE(window.findChild<QSpinBox *>("nrChroma")->value(), presets[preset - 1].second);
        }
        window.findChild<QPushButton *>("nrPreset4")->click();
        QVERIFY(window.findChild<QSpinBox *>("nrLuma")->isEnabled());
    }
    void batchControlsFollowCheckedTakes() {
        // Sin tomas no se puede convertir; el botón dice cuántas tomas se convertirán.
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        ConverterWindow window(store, temp.path() + "/missing-worker");
        auto *convert = window.findChild<QPushButton *>("startConversion");
        QVERIFY(!convert->isEnabled());
        QVERIFY(convert->text().contains('0'));
        QVERIFY(window.findChild<QTreeWidget *>("clipList"));
        QVERIFY(!window.findChild<QCheckBox *>("autoName"));   // un BRAW por toma, con nombre automático siempre
    }
    void cropNavigatorClampsAndResizes() {
        CropNavigator navigator({3072, 1728}); navigator.resize(960, 220); navigator.show();
        QSignalSpy moved(&navigator, &CropNavigator::cropChanged);
        QTest::mouseClick(&navigator, Qt::LeftButton, {}, {1, 1});
        QCOMPARE(navigator.crop().topLeft(), QPoint(0, 0));
        navigator.setCropSize({128, 192});
        QTest::mouseClick(&navigator, Qt::LeftButton, {}, {959, 219});
        QCOMPARE(navigator.crop(), QRect(2944, 1536, 128, 192));
        navigator.setCropSize({512, 512});
        QVERIFY(QRect(QPoint(0, 0), QSize(3072, 1728)).contains(navigator.crop()));
        QVERIFY(moved.count() >= 4);
    }
    void realNoiseComparisonAndMovableCrop() {
        const auto backend = qEnvironmentVariable("CDNG_TEST_BACKEND"), input = qEnvironmentVariable("CDNG_TEST_INPUT");
        if (backend.isEmpty() || input.isEmpty()) QSKIP("Set packaged worker and a calibrated sequence for noise comparison");
        initializeLanguage("en");
        ComparisonSettings settings{0., 0., 0, "none"};
        settings.resolutions = {{"original", "Original", {3840, 2160}}, {"3072x1728", "3K", {3072, 1728}}, {"1920x1080", "Full HD", {1920, 1080}}};
        settings.resolution = "3072x1728";
        NoiseDialog dialog(backend, input, 0, 2, {3072, 1728}, {}, settings);
        dialog.show();
        auto *service = dialog.findChild<PreviewService *>(); QVERIFY(service);
        QSignalSpy ready(service, &PreviewService::noiseReady), errors(service, &PreviewService::error);
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY2(errors.isEmpty(), errors.isEmpty() ? "" : qPrintable(errors.first().first().toString()));
        auto first = ready.takeLast();
        // Sin nada aplicado salvo la resolución: el original a 3840×2160 y la misma zona a 3072×1728 (×0,8).
        QCOMPARE(qvariant_cast<QImage>(first[1]).size(), QSize(256, 256)); QCOMPARE(qvariant_cast<QImage>(first[2]).size(), QSize(205, 205));
        for (const char *name : {"compareLuma", "compareChroma"}) dialog.findChild<QSpinBox *>(name)->setValue(50);
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY(errors.isEmpty()); auto filtered = ready.takeLast();
        QVERIFY(qvariant_cast<QImage>(filtered[1]) != qvariant_cast<QImage>(filtered[2]));
        auto *cropSize = dialog.findChild<QComboBox *>("noiseCropSize");
        cropSize->setCurrentIndex(cropSize->findData(QSize(128, 128)));
        auto *navigator = dialog.findChild<CropNavigator *>();
        QTest::mouseClick(navigator, Qt::LeftButton, {}, {navigator->width() - 1, navigator->height() - 1});
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY(errors.isEmpty()); auto moved = ready.takeLast();
        QCOMPARE(qvariant_cast<QImage>(moved[1]).size(), QSize(128, 128));
        const auto roi = moved[0].toJsonObject().value("roi").toArray();
        QCOMPARE(roi[0].toInt(), 3712); QCOMPARE(roi[1].toInt(), 2032);   // en píxeles del original (3840×2160)
        QCOMPARE(qvariant_cast<QImage>(moved[2]).size(), QSize(102, 102));  // la misma zona a 3072×1728
        auto *zoom = dialog.findChild<QComboBox *>("noiseZoom"); zoom->setCurrentIndex(zoom->findData(4));
        QCOMPARE(dialog.findChild<QLabel *>("noiseBefore")->size(), QSize(512, 512));
        QCOMPARE(dialog.findChild<QLabel *>("noiseAfter")->size(), QSize(512, 512));   // mismo tamaño en pantalla
        QCOMPARE(dialog.noise(), (QList<double>{50., 50.}));
        // Otro fotograma: mismo recorte, imagen distinta (el grano cambia de un frame a otro).
        dialog.findChild<QSlider *>("noiseFrame")->setValue(1);
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY(errors.isEmpty()); auto next = ready.takeLast();
        QCOMPARE(next[0].toJsonObject().value("roi").toArray(), roi);
        QVERIFY(qvariant_cast<QImage>(next[1]) != qvariant_cast<QImage>(moved[1]));
        // Exposición de visualización: las dos imágenes más claras, mismo recorte; la reducción no se repite.
        const auto mean = [](const QImage &image) { double sum = 0; for (int y = 0; y < image.height(); ++y)
            for (int x = 0; x < image.width(); ++x) sum += qGray(image.pixel(x, y)); return sum / (image.width() * image.height()); };
        dialog.findChild<QDoubleSpinBox *>("noiseExposure")->setValue(2);
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY(errors.isEmpty()); auto lifted = ready.takeLast();
        QCOMPARE(lifted[0].toJsonObject().value("exposure").toDouble(), 2.);
        QVERIFY(mean(qvariant_cast<QImage>(lifted[1])) > mean(qvariant_cast<QImage>(next[1])) * 1.5);
        QVERIFY(mean(qvariant_cast<QImage>(lifted[2])) > mean(qvariant_cast<QImage>(next[2])) * 1.5);
        // Compresión: el resultado pasa por el codificador y se decodifica; a 12:1 se aleja más del original que a 3:1.
        const auto distance = [](const QImage &a, const QImage &b) { double sum = 0; for (int y = 0; y < a.height(); ++y)
            for (int x = 0; x < a.width(); ++x) sum += std::abs(qGray(a.pixel(x, y)) - qGray(b.pixel(x, y))); return sum; };
        for (const char *name : {"compareLuma", "compareChroma"}) dialog.findChild<QSpinBox *>(name)->setValue(0);
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000); ready.clear();
        auto *mode = dialog.findChild<QComboBox *>("compareCompression"), *ratio = dialog.findChild<QComboBox *>("compareRatio");
        mode->setCurrentIndex(mode->findData("ratio")); ratio->setCurrentIndex(ratio->findData(3));
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY(errors.isEmpty()); auto light = ready.takeLast();
        QVERIFY(light[0].toJsonObject().value("compression").toObject().value("q").toInt() >= 4);
        ratio->setCurrentIndex(ratio->findData(12));
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY(errors.isEmpty()); auto heavy = ready.takeLast();
        QVERIFY(distance(qvariant_cast<QImage>(heavy[1]), qvariant_cast<QImage>(heavy[2])) > distance(qvariant_cast<QImage>(light[1]), qvariant_cast<QImage>(light[2])));
        QCOMPARE(dialog.settings().compression, QString("ratio")); QCOMPARE(dialog.settings().ratio, 12);
        // Presets: Media pone 35/60 y bloquea los valores; tocar un valor pasa a Personalizada.
        dialog.findChild<QPushButton *>("comparePreset2")->click();
        QCOMPARE(dialog.noise(), (QList<double>{35., 60.})); QVERIFY(!dialog.findChild<QSpinBox *>("compareLuma")->isEnabled());
        QCOMPARE(dialog.settings().preset, 2);
        dialog.findChild<QSpinBox *>("compareLuma")->setValue(40);
        QCOMPARE(dialog.settings().preset, int(CustomNoise)); QVERIFY(dialog.findChild<QSpinBox *>("compareLuma")->isEnabled());
        // Resolución: las dos imágenes se revelan al nuevo tamaño y el recorte queda dentro.
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000); ready.clear();
        auto *resolution = dialog.findChild<QComboBox *>("compareResolution");
        resolution->setCurrentIndex(resolution->findData("1920x1080"));
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 60000);
        QVERIFY(errors.isEmpty()); const auto small = ready.takeLast()[0].toJsonObject();
        QCOMPARE(small.value("width").toInt(), 1920); QCOMPARE(small.value("height").toInt(), 1080);
        // El original no cambia (mismo recorte nativo); el resultado es la misma zona a la mitad de píxeles.
        QCOMPARE(small.value("roi").toArray(), roi);
        const auto box = small.value("result_roi").toArray();
        QCOMPARE(box[2].toInt(), 64); QCOMPARE(box[3].toInt(), 64);
        QCOMPARE(box[0].toInt(), 1856); QCOMPARE(box[1].toInt(), 1016);
        QVERIFY(dialog.findChild<QLabel *>("compareAfterTitle")->text().contains("1920"));
        QCOMPARE(dialog.settings().resolution, QString("1920x1080"));
        const auto screenshot = qEnvironmentVariable("CDNG_TEST_NOISE_SCREENSHOT");
        if (!screenshot.isEmpty()) QVERIFY(dialog.grab().save(screenshot));
        dialog.reject(); QCOMPARE(dialog.result(), int(QDialog::Rejected));
        initializeLanguage("es");
    }
    void realPreviewMetadataAndLatestFrame() {
        const auto backend = qEnvironmentVariable("CDNG_TEST_BACKEND");
        const auto input = qEnvironmentVariable("CDNG_TEST_INPUT");
        if (backend.isEmpty() || input.isEmpty()) QSKIP("Set packaged worker and a short real sequence for preview validation");
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        QVERIFY(store.install().ok);
        ConverterWindow window(store, backend); window.show();
        auto *service = window.findChild<PreviewService *>(); QVERIFY(service);
        QSignalSpy ready(service, &PreviewService::ready), errors(service, &PreviewService::error);
        auto *folder = window.findChild<QLineEdit *>("inputFolder"); folder->setText(input);
        QVERIFY(QMetaObject::invokeMethod(folder, "editingFinished", Qt::DirectConnection));
        QTRY_VERIFY_WITH_TIMEOUT(ready.count() > 0 || errors.count() > 0, 45000);
        QVERIFY2(errors.isEmpty(), errors.isEmpty() ? "" : qPrintable(errors.first().first().toString()));
        const auto first = ready.takeFirst(); const auto metadata = first[0].toJsonObject();
        QVERIFY(metadata.value("total").toInt() >= 4);
        QCOMPARE(metadata.value("camera").toString(), QString("SIGMA fp"));
        QVERIFY(!qvariant_cast<QImage>(first[1]).isNull());
        QVERIFY(window.findChild<QLabel *>("clipDetails")->text().contains("3840"));
        QTRY_COMPARE(window.findChild<QTreeWidget *>("clipList")->topLevelItemCount(), 1);
        QVERIFY(window.findChild<QSlider *>("previewFrame")->isEnabled());
        window.findChild<QPushButton *>("ratio12")->click();
        QVERIFY(window.findChild<QLabel *>("compressionEstimate")->text().contains("MB"));
        QVERIFY(window.findChild<QLabel *>("outputNaming")->text().contains("_12to1.braw"));
        // A stale reply for frame 1 must never overwrite the newest requested frame.
        service->request(input, 1); service->request(input, 2); service->request(input, 3);
        QTRY_VERIFY_WITH_TIMEOUT(ready.count() > 0 || errors.count() > 0, 45000);
        QVERIFY(errors.isEmpty()); QCOMPARE(ready.count(), 1);
        QCOMPARE(ready.takeFirst()[0].toJsonObject().value("frame").toInt(), 3);
        QCOMPARE(window.findChild<QSlider *>("previewFrame")->value(), 3);
        const auto screenshot = qEnvironmentVariable("CDNG_TEST_PREVIEW_SCREENSHOT");
        if (!screenshot.isEmpty()) QVERIFY(window.grab().save(screenshot));
        // Dialog settings are committed by Apply; closing discards its edits.
        QTimer::singleShot(0, &window, [&window] {
            auto *dialog = window.findChild<NoiseDialog *>();
            if (dialog) { dialog->findChild<QSpinBox *>("compareLuma")->setValue(35); dialog->accept(); }
        });
        window.findChild<QPushButton *>("compareNoise")->click();
        QCOMPARE(window.findChild<QSpinBox *>("nrLuma")->value(), 35);
        QTimer::singleShot(0, &window, [&window] {
            auto *dialog = window.findChild<NoiseDialog *>();
            if (dialog) { dialog->findChild<QSpinBox *>("compareLuma")->setValue(90); dialog->reject(); }
        });
        window.findChild<QPushButton *>("compareNoise")->click();
        QCOMPARE(window.findChild<QSpinBox *>("nrLuma")->value(), 35);
        window.close();
    }
    void unsupportedResourcesRemainInspectable() {
        const auto backend = qEnvironmentVariable("CDNG_TEST_BACKEND"), input = qEnvironmentVariable("CDNG_TEST_RESOURCES");
        if (backend.isEmpty() || input.isEmpty()) QSKIP("Set a real unsupported source folder to inspect its UI");
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        ConverterWindow window(store, backend); window.show();
        auto *service = window.findChild<PreviewService *>();
        QSignalSpy ready(service, &PreviewService::ready), errors(service, &PreviewService::error);
        auto *folder = window.findChild<QLineEdit *>("inputFolder"); folder->setText(input);
        QVERIFY(QMetaObject::invokeMethod(folder, "editingFinished", Qt::DirectConnection));
        QTRY_VERIFY_WITH_TIMEOUT(!ready.isEmpty() || !errors.isEmpty(), 45000);
        QVERIFY2(errors.isEmpty(), errors.isEmpty() ? "" : qPrintable(errors.first().first().toString()));
        const auto metadata = ready.last()[0].toJsonObject();
        QVERIFY(!metadata.value("compatible").toBool());
        QCOMPARE(metadata.value("iso").toInt(), 400);
        QVERIFY(!qvariant_cast<QImage>(ready.last()[1]).isNull());
        const auto screenshot = qEnvironmentVariable("CDNG_TEST_RESOURCE_SCREENSHOT");
        if (!screenshot.isEmpty()) QVERIFY(window.grab().save(screenshot));
    }
    void droppingAFolderOrADngSelectsTheSequence() {
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        ConverterWindow window(store, temp.path() + "/missing-worker");
        QDir(temp.path()).mkdir("take"); QFile dng(temp.path() + "/take/A001_000001.DNG"); QVERIFY(dng.open(QIODevice::WriteOnly)); dng.close();
        auto drop = [&](const QList<QUrl> &urls) {
            QMimeData mime; mime.setUrls(urls);
            QDragEnterEvent enter(QPoint(10, 10), Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
            QApplication::sendEvent(&window, &enter);
            QDropEvent event(QPoint(10, 10), Qt::CopyAction, &mime, Qt::LeftButton, Qt::NoModifier);
            QApplication::sendEvent(&window, &event);
            return enter.isAccepted();
        };
        QVERIFY(drop({QUrl::fromLocalFile(temp.path() + "/take")}));
        QCOMPARE(window.findChild<QLineEdit *>("inputFolder")->text(), QFileInfo(temp.path() + "/take").absoluteFilePath());
        window.findChild<QLineEdit *>("inputFolder")->clear();
        QVERIFY(drop({QUrl::fromLocalFile(temp.path() + "/take/A001_000001.DNG")}));
        QCOMPARE(window.findChild<QLineEdit *>("inputFolder")->text(), QFileInfo(temp.path() + "/take").absoluteFilePath());
        window.findChild<QLineEdit *>("inputFolder")->clear();
        QFile other(temp.path() + "/notes.txt"); QVERIFY(other.open(QIODevice::WriteOnly)); other.close();
        QVERIFY(!drop({QUrl::fromLocalFile(temp.path() + "/notes.txt")}));
        QVERIFY(!drop({QUrl::fromLocalFile(temp.path() + "/take"), QUrl::fromLocalFile(temp.path() + "/notes.txt")}));
        QVERIFY(window.findChild<QLineEdit *>("inputFolder")->text().isEmpty());
    }
    void unavailableMotorDoesNotStartOrOverwrite() {
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        QVERIFY(store.install().ok);
        ConverterWindow window(store, temp.path() + "/missing-worker");
        auto *folder = window.findChild<QLineEdit *>("inputFolder"); folder->setText(temp.path());
        QVERIFY(QMetaObject::invokeMethod(folder, "editingFinished", Qt::DirectConnection));
        // Sin motor no se exploran tomas: Origen lo dice y no hay nada que convertir ni fichero creado.
        QTRY_VERIFY(window.findChild<QLabel *>("sourceCompatibility")->text().contains("motor"));
        QVERIFY(!window.findChild<QPushButton *>("startConversion")->isEnabled());
        window.findChild<QPushButton *>("startConversion")->click();
        QVERIFY(!window.findChild<QPushButton *>("cancelConversion")->isEnabled());
        QVERIFY(QDir(temp.path()).entryList({"*.braw"}).isEmpty());
    }
    void realPackagedWorkerThroughWidgets() {
        const auto backend = qEnvironmentVariable("CDNG_TEST_BACKEND");
        const auto input = qEnvironmentVariable("CDNG_TEST_INPUT");
        if (backend.isEmpty() || input.isEmpty()) QSKIP("Set CDNG_TEST_BACKEND and CDNG_TEST_INPUT for the packaged conversion test");
        QTemporaryDir temp;
        ProfileStore store({temp.path() + "/support", temp.path() + "/backups", true});
        QVERIFY(store.install().ok);
        ConverterWindow window(store, backend);
        window.show();
        window.findChild<QRadioButton *>("qualityMode")->click();
        auto *folder = window.findChild<QLineEdit *>("inputFolder"); folder->setText(input);
        QVERIFY(QMetaObject::invokeMethod(folder, "editingFinished", Qt::DirectConnection));
        QTRY_VERIFY_WITH_TIMEOUT(window.findChild<QTreeWidget *>("clipList")->topLevelItemCount() == 1, 45000);
        window.findChild<QLineEdit *>("outputFolder")->setText(temp.path() + "/braw");
        auto *job = window.findChild<ConversionJob *>(); QVERIFY(job);
        QSignalSpy spy(job, &ConversionJob::finished);
        window.findChild<QPushButton *>("startConversion")->click();
        QVERIFY(job->busy());
        QVERIFY(!window.findChild<QPushButton *>("startConversion")->isEnabled());
        QVERIFY(window.findChild<QPushButton *>("cancelConversion")->isEnabled());
        QVERIFY(spy.wait(120000));
        const auto result = spy.takeFirst();
        QVERIFY2(result[0].toBool(), qPrintable(result[2].toString()));
        const auto outputs = QDir(temp.path() + "/braw").entryList({"*.braw"});
        QCOMPARE(outputs.size(), 1);
        QVERIFY(QFileInfo(temp.path() + "/braw/" + outputs.first()).size() > 0);
        QTRY_VERIFY(window.findChild<QLabel *>("conversionState")->text().contains("1 convertidas"));
        const auto screenshot = qEnvironmentVariable("CDNG_TEST_SCREENSHOT");
        if (!screenshot.isEmpty()) QVERIFY(window.grab().save(screenshot));
        // Repetir el lote salta lo que ya existe, sin sobrescribir.
        window.findChild<QPushButton *>("startConversion")->click();
        QVERIFY(!job->busy());
        QVERIFY(window.findChild<QLabel *>("conversionState")->text().contains("1 saltadas"));
        window.findChild<QLineEdit *>("outputFolder")->setText(temp.path() + "/cancelled");
        connect(job, &ConversionJob::progress, &window, [&window] {
            window.findChild<QPushButton *>("cancelConversion")->click();
        });
        window.findChild<QPushButton *>("startConversion")->click();
        QVERIFY(spy.wait(120000));
        const auto cancelled = spy.takeFirst();
        QVERIFY2(cancelled[1].toBool(), qPrintable(cancelled[2].toString()));
        QVERIFY(QDir(temp.path() + "/cancelled").entryList({"*.braw"}).isEmpty());
        QVERIFY(window.findChild<QLabel *>("conversionState")->text().contains("cancelado"));
    }
};
QTEST_MAIN(ConverterWindowTests)
#include "converter_window_test.moc"
