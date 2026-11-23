// cdng-convert: proceso de trabajo de la app Qt (sustituye al worker Python congelado). Ver worker.hpp.
#include "worker.hpp"

#include <cstdlib>
#include <cstring>
#include <iostream>

int main(int argc, char **argv) {
    // LibRaw usa OpenMP: con varios frames en paralelo, cada revelado lanzaría hilos en todos los núcleos y los
    // procesos se pisarían (medido: 11 núcleos ocupados con 4 procesos y 8 veces más lento). El paralelismo lo dan
    // los frames, como en el worker Python, que también fijaba OMP_NUM_THREADS=1.
#ifdef _WIN32
    _putenv_s("OMP_NUM_THREADS", "1");
#else
    setenv("OMP_NUM_THREADS", "1", 1);
#endif
    using namespace cdng::worker;
    try {
        cdng::prepareLibRaw();   // antes de los hilos de la exploración y de la conversión (ver dng.hpp)
        if (argc == 2 && std::strcmp(argv[1], "--self-test") == 0) {
            const Engine engine(cdng::resourceRoot());
            checkResources(engine.root);
            emit("ready", cdng::json::Object().str("encoder", "native").str("profile", "auto (va | og | pocket)").boolean("frozen", true));
            return 0;
        }
        if (argc == 2 && std::strcmp(argv[1], "--preview-server") == 0) return previewServer();
        if (argc == 3 && std::strcmp(argv[1], "--request") == 0) return convert(argv[2]);
        std::cerr << "uso: cdng-convert --request FICHERO | --preview-server | --self-test\n";
        return 2;
    } catch (const Cancelled &) {
        emit("cancelled");
        return 3;
    } catch (const std::exception &error) {
        std::cerr << error.what() << '\n';
        emit("error", cdng::json::Object().str("message", t(error.what())));
        return 1;
    }
}
