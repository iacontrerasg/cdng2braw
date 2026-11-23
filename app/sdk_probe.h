#pragma once

#include <QString>
#include <QStringList>

namespace cdng {
// Comprobación de que el decodificador BRAW de Resolve lee nuestro perfil (solo Windows). Carga el
// BlackmagicRawAPI.dll de Resolve en ESTE proceso, observa en su tabla de importaciones las llamadas a
// FindFirstFileExW/CreateFileW y crea un códec, que es cuando el SDK recorre Camera Support. No modifica nada
// de Resolve ni de la DLL en disco. Se ejecuta en un proceso aparte (--probe-camera-support): la DLL arranca
// hilos y no se descarga limpiamente.
struct SdkProbe {
    QString dll;            // DLL usada; vacía si no se encontró Resolve
    bool codec = false;     // CreateCodec funcionó
    QStringList listed;     // carpetas Camera Support recorridas (barras normales)
    QStringList read;       // ficheros abiertos dentro de Camera Support
    QString error;
};
SdkProbe probeCameraSupport(const QString &dll);
}
