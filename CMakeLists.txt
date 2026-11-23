cmake_minimum_required(VERSION 3.20)
project(cdng_converter VERSION 0.1.0 LANGUAGES CXX)

include(CTest)
# Las fuentes son UTF-8 sin BOM. Sin /utf-8, MSVC las lee en la página de códigos del sistema (CP1252) y cambia
# los bytes de los literales con acentos y comillas tipográficas. No es cosmético: esos literales son las claves
# con las que app/i18n.cpp busca la traducción (QString::fromUtf8(source) contra i18n/es.json), así que el español
# dejaría de encontrarse en silencio. Va aquí para que alcance a todos los objetivos, incluidas las pruebas.
add_compile_options("$<$<CXX_COMPILER_ID:MSVC>:/utf-8>")
option(CDNG_SANITIZERS "Enable address and undefined behaviour sanitizers" OFF)
option(CDNG_PYTHON_TESTS "Register Python/reference integration tests (requires requirements.txt)" OFF)
option(CDNG_BUILD_APP "Build the Qt converter and camera profile manager" OFF)
add_subdirectory(native)
if(CDNG_BUILD_APP)
    add_subdirectory(app)
endif()
if(BUILD_TESTING AND CDNG_PYTHON_TESTS)
    find_package(Python3 REQUIRED COMPONENTS Interpreter)
    add_test(NAME native_reference
        COMMAND "${Python3_EXECUTABLE}" -m unittest discover -s tests -v)
    set_tests_properties(native_reference PROPERTIES
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
        ENVIRONMENT "CDNG_NATIVE_LIBRARY=$<TARGET_FILE:cdng_native>;CDNG_REQUIRE_NATIVE_TESTS=1")
endif()
