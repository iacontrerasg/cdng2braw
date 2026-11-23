; Instalador de Windows del conversor CinemaDNG -> BRAW (Inno Setup).
;
;     iscc packaging\cdng2braw.iss
;
; Primero hay que dejar el paquete portable: `.\build.ps1 package` compila el motor, el proceso de trabajo y la
; app Qt, pasa windeployqt y deja out\cdng2braw-win64\ con el ejecutable, las DLL de Qt y de LibRaw, converter\
; y los avisos de terceros; además escribe out\version.txt. Esto envuelve esa carpeta en un Setup. Son los mismos
; bytes que lleva el ZIP: esta es la versión que te la descomprime.
;
; Mismo convenio que los otros proyectos (eclipse-aligner, film-developer): Inno Setup, AppId fijo para que las
; actualizaciones sustituyan en sitio, instalación por usuario sin pedir administrador, y la versión leída de un
; único fichero en vez de repetida aquí.
;
; **Sin firmar.** El Setup.exe hace saltar SmartScreen la primera vez; un certificado de firma es un coste anual
; que este proyecto no lleva.
;
; A diferencia de los otros, aquí no hay Python: el ejecutable es C++ con Qt, y el motor es un segundo ejecutable
; (converter\cdng-convert.exe) que la app lanza como proceso aparte.

#define AppName "CDNG to BRAW Converter"
#define ExeName "cdng2braw.exe"
#define SrcDir  "..\out\cdng2braw-win64"
; La versión que build.ps1 escribió en out\version.txt: un solo sitio que editar.
#define AppVer  Trim(FileRead(FileOpen("..\out\version.txt")))

[Setup]
; AppId fijo: así una versión nueva reemplaza la anterior en lugar de acumular instalaciones.
AppId={{B7C4E2A9-5D18-4F63-9E07-3A6B1C8D4F25}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=El Cacharrista
AppPublisherURL=https://elcacharrista.com
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#ExeName}
; Por usuario por omisión: sin aviso de administrador, va a Local AppData. La app no necesita privilegios, porque
; convierte por la ruta Video Assist y no instala ningún perfil en Camera Support.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
; El motor es x64 y las DLL de vcpkg también: no hay versión de 32 bits.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\out
OutputBaseFilename=cdng2braw-{#AppVer}-x64-setup
Compression=lzma2
SolidCompression=no
WizardStyle=modern

[Languages]
; La app elige idioma según el sistema (inglés o español): el instalador ofrece los mismos dos.
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Toda la carpeta portable, recursiva: recursesubdirs+createallsubdirs mantiene plugins\ (sin el plugin de
; plataforma la app no arranca) y converter\ con model\, que el motor comprueba al empezar.
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Inno solo sigue la pista de lo que instaló. La app guarda preferencias en el registro, no aquí, pero el motor
; puede dejar ficheros de trabajo en su carpeta: se borra el árbol completo.
Type: filesandordirs; Name: "{app}"
