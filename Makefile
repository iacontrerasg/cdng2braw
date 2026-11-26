# Construcción del conversor CinemaDNG → BRAW (macOS Apple Silicon; Qt 5 de Homebrew, CMake ≥ 3.20, Python 3.9 o posterior).
#
#   make              compila el motor C++ y la app Qt (build/qt-app)
#   make test         ejecuta las suites de CTest (motor nativo y app, incluidas las pruebas Python)
#   make package      compila el proceso de trabajo C++ (estático) y empaqueta la app redistribuible en $(OUT)
#   make install      copia la app empaquetada a $(INSTALL_DIR) (por defecto /Applications)
#   make run          abre la app empaquetada;  make run-dev abre la del árbol de build
#   make brawdec      compila el oráculo sobre el SDK de Blackmagic RAW (tools/brawdec/brawdec)
#   make venv         crea .venv con requirements.txt (las herramientas de análisis; compilar y empaquetar no lo necesitan)
#   make benchmark    banco de fidelidad (serie A completa; ver docs/image-fidelity-benchmark.md)
#   make clean        borra los árboles de build;  make distclean borra además el paquete
#
# Variables ajustables: make install INSTALL_DIR=~/Applications ; make package OUT=out/otro ; make JOBS=8
#                       make benchmark DATASET=~/Desktop/BMD REFERENCES=~/Desktop/EXR SERIES=A TEST=A02

# Los scripts de empaquetado y las pruebas de la app solo usan la biblioteca estándar: sirve el python3 del sistema.
# Con .venv (make venv), se usa ese. El banco de fidelidad también acepta out/.venv-exr si .venv no tiene OpenEXR.
PYTHON      ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(if $(wildcard out/.venv-exr/bin/python),out/.venv-exr/bin/python,python3))
QT          ?= /opt/homebrew/opt/qt@5
BUILD       ?= build/qt-app
NATIVE      ?= build/native
WORKER      ?= build/worker
OUT         ?= out/cdng2braw
INSTALL_DIR ?= /Applications
JOBS        ?= 4
APP          = cdng2braw.app
DATASET     ?= $(HOME)/Desktop/BMD
REFERENCES  ?=
SERIES      ?= A
TEST        ?=
RUN         ?=
BASELINE    ?=
BENCH_OUT   ?= out/benchmark
CONVERTER   ?= $(WORKER)/converter

.PHONY: all help venv native configure app test worker package install uninstall run run-dev brawdec \
	benchmark benchmark-validate benchmark-equivalence benchmark-freeze fidelity-test clean distclean

all: app

help:
	@sed -n '2,14p' Makefile | sed 's/^# \{0,1\}//'

# ---- entorno Python -------------------------------------------------------------------------
venv:
	@test -x .venv/bin/python || { python3 -m venv .venv && .venv/bin/pip install -U pip; }
	@.venv/bin/python -c "import numpy, scipy, rawpy, PIL, OpenEXR" 2>/dev/null || .venv/bin/pip install -r requirements.txt

# ---- motor C++ (codificador de entropía) y app Qt --------------------------------------------
native:
	cmake -S . -B $(NATIVE) -DCMAKE_BUILD_TYPE=Release
	cmake --build $(NATIVE) --parallel $(JOBS)

configure:
	cmake -S . -B $(BUILD) -DCMAKE_BUILD_TYPE=Release -DCDNG_BUILD_APP=ON \
	  -DCMAKE_PREFIX_PATH=$(QT) -DCDNG_PYTHON_TESTS=ON -DPython3_EXECUTABLE=$(if $(wildcard $(PYTHON)),$(abspath $(PYTHON)),$(shell command -v $(PYTHON)))

app: configure
	cmake --build $(BUILD) --parallel $(JOBS)

test: native app
	ctest --test-dir $(NATIVE) --output-on-failure
	ctest --test-dir $(BUILD) --output-on-failure

# ---- paquete redistribuible -------------------------------------------------------------------
# Proceso de trabajo C++ con LibRaw y sus dependencias enlazadas de forma estática (sin Python ni PyInstaller).
worker:
	cmake -S . -B $(WORKER) -DCMAKE_BUILD_TYPE=Release -DCDNG_STATIC_DEPS=ON -DBUILD_TESTING=OFF
	cmake --build $(WORKER) --parallel $(JOBS) --target cdng-convert
	$(PYTHON) tools/stage_converter.py --binary $(WORKER)/bin/cdng-convert --out $(WORKER)/converter

package: app worker
	@test -n "$(OUT)" && rm -rf "$(OUT)"
	$(PYTHON) tools/package_profile_app.py --build-dir $(BUILD) --out $(OUT) \
	  --macdeployqt $(QT)/bin/macdeployqt --converter $(WORKER)/converter
	@echo "Paquete: $(OUT)/$(APP)"

install: package
	rm -rf "$(INSTALL_DIR)/$(APP)"
	cp -R "$(OUT)/$(APP)" "$(INSTALL_DIR)/"
	@echo "Instalada en $(INSTALL_DIR)/$(APP)"

uninstall:
	rm -rf "$(INSTALL_DIR)/$(APP)"

run:
	open "$(OUT)/$(APP)"

run-dev: app
	open "$(BUILD)/app/$(APP)"

# ---- oráculo (requiere /Library/Frameworks/BlackmagicRawAPI.framework) ------------------------
brawdec: tools/brawdec/brawdec

tools/brawdec/brawdec: tools/brawdec/brawdec.cpp
	clang++ -std=c++17 -O1 $< -o $@ -framework CoreFoundation

# ---- banco de fidelidad de imagen --------------------------------------------------------------
# Infraestructura en tools/fidelity; manifiesto en benchmark/. Resultados en out/benchmark/ (ignorado por Git).
# Documentación: docs/image-fidelity-benchmark.md
#
#   make benchmark                         serie A completa (worker + brawdec + EXR de Resolve)
#   make benchmark SERIES=A TEST=A02       una toma
#   make benchmark BASELINE=main-352cfab   además, regresión frente a un baseline congelado
#   make benchmark-validate                manifiesto y dependencias
#   make benchmark-equivalence             brawdec frente a EXR del mismo BRAW (sin conversión)
#   make benchmark-freeze RUN=… NAME=…     congela una ejecución como baseline
#   make fidelity-test                     pruebas unitarias del banco (sin dataset)

_BENCH_COMMON = --dataset "$(DATASET)" --out "$(BENCH_OUT)" --converter "$(CONVERTER)"
ifneq ($(REFERENCES),)
_BENCH_COMMON += --references "$(REFERENCES)"
endif
ifneq ($(RUN),)
_BENCH_COMMON += --run "$(RUN)"
endif
ifneq ($(MANIFEST),)
_BENCH_COMMON += --manifest "$(MANIFEST)"
endif
_BENCH_RUN = $(_BENCH_COMMON)
ifneq ($(SERIES),)
_BENCH_RUN += --series "$(SERIES)"
endif
ifneq ($(TEST),)
_BENCH_RUN += --test "$(TEST)"
endif
ifneq ($(BASELINE),)
_BENCH_RUN += --baseline "$(BASELINE)"
endif

benchmark-validate: brawdec
	$(PYTHON) -m tools.fidelity validate $(_BENCH_COMMON)

benchmark: worker brawdec
	$(PYTHON) -m tools.fidelity run $(_BENCH_RUN)

benchmark-equivalence: brawdec
	$(PYTHON) -m tools.fidelity equivalence $(_BENCH_COMMON)

benchmark-freeze:
	@test -n "$(RUN)" && test -n "$(NAME)" || { echo 'uso: make benchmark-freeze RUN=<id> NAME=<nombre>'; exit 2; }
	$(PYTHON) -m tools.fidelity freeze "$(BENCH_OUT)/runs/$(RUN)" "$(NAME)" --out "$(BENCH_OUT)"

fidelity-test:
	$(PYTHON) -m unittest tests.test_fidelity tests.test_exp005 -v

# ---- limpieza ----------------------------------------------------------------------------------
clean:
	rm -rf $(BUILD) $(NATIVE)

distclean: clean
	@test -n "$(OUT)" && rm -rf "$(OUT)"
