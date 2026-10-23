# Yamaha RM1x R1S → MIDI

Conversor de canciones `.R1S` del secuenciador Yamaha RM1x a archivos MIDI estándar de tipo 1. Está escrito en Python y no necesita dependencias para convertir. La estructura binaria del R1S se ha deducido a partir de archivos reales; Yamaha documenta las voces y funciones del equipo, pero no el formato interno del archivo.

## Uso

Requiere Python 3.10 o posterior.

```bash
python r1s_to_midi.py -o converted_mid_improved /ruta/a/las/canciones/*.R1S
```

Esta salida conserva los bancos, programas, alturas y canales derivados de las pistas RM1x, junto con los ajustes XG que se han podido recuperar. Para escuchar las voces propias del RM1x hace falta un generador de sonido compatible.

Para una copia orientada a un reproductor General MIDI:

```bash
python r1s_to_midi.py --gm-drums -o converted_mid_gm /ruta/a/las/canciones/*.R1S
```

`--gm-drums` lleva las baterías y voces individuales de percusión reconocidas al canal 10 y las aproxima con notas de batería GM. Es una copia para escucha: algunos sonidos, como gong, taiko, udu, campanillas o redoble inverso, no tienen un equivalente idéntico. La selección depende del banco, subbanco y programa activos al inicio de cada nota, nunca del nombre del archivo ni de la repetición de notas. Las voces afinables del mismo banco permanecen en pistas melódicas.

Cada entrada crea un `.mid` con el mismo nombre base dentro del directorio indicado por `-o`. La opción `--verbose` muestra el número de notas por pista. `--bpm` fija un tempo de reserva si no se encuentra el registro de ajustes de la canción.

## Datos recuperados

- Las 16 pistas, notas, duraciones, velocidades, controles MIDI, pitch bend, cambios de banco/programa y SysEx grabados.
- Tempo y compás, incluidos sus cambios durante la canción, con resolución de 480 ticks por negra. Los códigos de compás verificados en los archivos disponibles corresponden a 2/4, 3/4, 4/4, 5/4 y 6/4.
- Banco y programa iniciales, volumen, panorama, expresión y envíos de reverb, chorus y variación por pista.
- Tipos iniciales de reverb, chorus y variación en mensajes Yamaha XG.

Los eventos R1S `F3`, cuyo significado aún no se conoce, se conservan como metadatos MIDI específicos del secuenciador.

## Límites conocidos

El conversor aún no aplica los ajustes de Play FX y Out Channel, la edición de voces ni todos los parámetros detallados de efectos. Asigna el canal MIDI por número de pista; por ello, una configuración de salida especial del RM1x podría sonar de forma distinta. Los códigos de compás con denominadores no verificados producen un aviso en lugar de interpretarse por conjetura.

La copia GM cambia timbres propios del aparato por aproximaciones. Si una voz de percusión se interpretó a distintas alturas, asignarla a una nota de batería GM puede perder esa diferencia; la salida orientada a XG conserva las alturas de origen. No se ha comparado aún una misma canción R1S con un MIDI exportado por el propio RM1x, por lo que no se puede garantizar reproducción idéntica.

Referencias: [manual del RM1x](https://jp.yamaha.com/files/download/other_assets/2/319182/RM1xE1.pdf) y [lista de voces e implementación MIDI](https://jp.yamaha.com/files/download/other_assets/3/317923/RM1xE2.pdf).

## Pruebas

```bash
python -m unittest -v test_r1s_to_midi.py
```

Las pruebas sintéticas funcionan sin archivos de canciones. Si se colocan archivos `.R1S` en la raíz del repositorio, también se comprueba automáticamente su conversión completa en ambos modos. Con `mido` instalado, estas pruebas adicionales verifican asimismo la estructura y los eventos del MIDI generado. Los archivos de canciones no se incluyen en el repositorio.
