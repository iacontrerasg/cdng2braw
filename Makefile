# Construcción del conversor CinemaDNG → BRAW (macOS Apple Silicon; Qt 5 de Homebrew, CMake ≥ 3.20, Python 3.9 o posterior).
#
#   make              compila el motor C++ y la app Qt (build/qt-app)
#   make test         ejecuta las suites de CTest (motor nativo y app, incluidas las pruebas Python)
#   make package      compila el proceso de trabajo C++ (estático) y empaqueta la app redistribuible en $(OUT)
#   make install      copia la app empaquetada a $(INSTALL_DIR) (por defecto /Applications)
#   make run          abre la app empaquetada;  make run-dev abre la del árbol de build
#   make brawdec      compila el oráculo sobre el SDK de Blackmagic RAW (tools/brawdec/brawdec)
#   make venv         crea .venv con requirements.txt (las herramientas de análisis; compilar y empaquetar no lo necesitan)
#   make clean        borra los árboles de build;  make distclean borra además el paquete
#
# Variables ajustables: make install INSTALL_DIR=~/Applications ; make package OUT=out/otro ; make JOBS=8

# Los scripts de empaquetado y las pruebas de la app solo usan la biblioteca estándar: sirve el python3 del sistema.
# Con .venv (make venv), se usa ese.
PYTHON      ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
QT          ?= /opt/homebrew/opt/qt@5
BUILD       ?= build/qt-app
NATIVE      ?= build/native
WORKER      ?= build/worker
OUT         ?= out/cdng2braw
INSTALL_DIR ?= /Applications
JOBS        ?= 4
APP          = cdng2braw.app

.PHONY: all help venv native configure app test worker package install uninstall run run-dev brawdec clean distclean

all: app

help:
	@sed -n '2,13p' Makefile | sed 's/^# \{0,1\}//'

# ---- entorno Python -------------------------------------------------------------------------
venv:
	@test -x .venv/bin/python || { python3 -m venv .venv && .venv/bin/pip install -U pip; }
	@.venv/bin/python -c "import numpy, scipy, rawpy, PIL" 2>/dev/null || .venv/bin/pip install -r requirements.txt

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

# ---- limpieza ----------------------------------------------------------------------------------
clean:
	rm -rf $(BUILD) $(NATIVE)

distclean: clean
	@test -n "$(OUT)" && rm -rf "$(OUT)"
