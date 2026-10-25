/** Spanish locale — must satisfy every key in the Chinese baseline.
 *
 * Copy this file to add another language; `Strings` makes the compiler point at
 * whatever has not been translated yet.
 */

import type { Strings } from "./zh";

export const es: Strings = {
  // --- shell ---------------------------------------------------------------
  "app.title": "Rancho · Simulador de cultivos",
  "brand.sub": "Simulador de cultivos",
  "lang.label": "Idioma",

  // --- top bar -------------------------------------------------------------
  "field.scenario": "Escenario",
  "btn.run": "Ejecutar simulación",
  "status.connecting": "Conectando con el servicio…",
  "status.connected": "Servicio conectado · v{0}",
  "status.noBackend": "Backend no conectado (ejecuta primero rancho serve)",
  "status.dirtyHasBundle": "Configuración cambiada · pulsa Ejecutar para repetir",
  "status.dirtyNoBundle": "Pendiente · pulsa Ejecutar para comenzar",
  "status.running": "Ejecutando la simulación…",
  "status.ready": "Listo · {0} días · {1} s",
  "status.failed": "La simulación falló",

  // --- right panel: sections ----------------------------------------------
  "title.channels": "Canal de color",
  "title.legend": "Leyenda",
  "title.metrics": "Estado del día",
  "title.layout": "Distribución de cultivos",
  "title.size": "Tamaño de la parcela",
  "title.field": "Parcela",
  "title.yield": "Rendimiento",
  "title.trajectory": "Trayectoria de la temporada",

  // --- layout / painting ---------------------------------------------------
  "layout.toggle": "Arrastrar para pintar (si se desactiva, se gira la vista)",
  "layout.enabledHint":
    "Haz clic o arrastra una celda para cambiar su cultivo; se vuelve a ejecutar automáticamente.",
  "layout.disabledHint":
    "La fenología de este escenario la avanza por parcela el complemento CropForge; la mezcla por celda está desactivada.",
  "btn.fillAll": "Rellenar todo",
  "btn.fillBands": "Bandas por columna",
  "btn.reset": "Restablecer",

  // --- field size ----------------------------------------------------------
  "slider.rows": "Filas",
  "slider.cols": "Columnas",
  "slider.days": "Días",
  "grid.hintLarge": " · cuadrícula grande, una ejecución tarda de 10 s a un minuto",
  "grid.hintDrag": " (se vuelve a ejecutar al soltar)",
  "days.manual": "Fijado manualmente en {0} días · {1} madura en {2} días",
  "days.follow": "Sigue al cultivo más largo: {0} madura en {1} días",
  "crop.fallback": "cultivo",

  // --- run / overlays ------------------------------------------------------
  "run.overlay": "Ejecutando la simulación: {0} × {1} celdas · {2} días…",
  "overlay.connecting": "Conectando con el servicio de simulación…",
  "overlay.noBackend":
    "No se puede acceder al servicio de simulación. Ejecuta rancho serve en la raíz del proyecto y actualiza la página.",
  "overlay.presetsFail": "Error al leer los escenarios: {0}",
  "overlay.failed": "La simulación falló: {0}",

  // --- yield ---------------------------------------------------------------
  "yield.total": "Total",
  "yield.gross": "Producción total",
  "yield.biomass": "Biomasa",
  "yield.harvestIndex": "Índice de cosecha",
  "yield.perCrop": "└ rendimiento",

  // --- legend --------------------------------------------------------------
  "legend.crops": "{0} cultivos",
  "legend.stages": "{0} etapas",

  // --- field info ----------------------------------------------------------
  "field.field": "Parcela",
  "field.area": "Superficie",
  "field.crop": "Cultivo",
  "field.variety": "Variedad",
  "field.days": "Días",
  "field.site": "Sitio",
  "field.density": "Densidad",
  "field.irrigation": "Riego",
  "field.fert": "Nitrógeno aplicado",
  "field.mixed": "{0} cultivos mezclados",
  "field.shelter": "Cubierta",
  "field.shelterVal": "Cubierta · transmisión {0}%",
  "field.weather": "Clima",
  "field.weatherObserved": "observado",
  "field.weatherSynthetic": "sintético",
  "field.irrigDrip": "riego {0} mm · cobertura {1}%",
  "field.irrigRainfed": "secano",

  // --- soil / terrain enums (keyed by the server's raw value) --------------
  "soil.loam": "Franco",
  "soil.clay": "Arcilloso",
  "soil.sandy": "Franco arenoso",
  "soil.andisol": "Andisol",
  "terrain.flat": "Llano",
  "terrain.slope": "Pendiente",
  "terrain.undulating": "Ondulado",

  // --- metrics -------------------------------------------------------------
  "metric.spatial": "Rango espacial",
  "metric.biomass": "Biomasa",
  "metric.lai": "Área foliar",
  "metric.moisture": "Humedad",
  "metric.stress": "Índice de estrés",
  "metric.temp": "Temperatura",
  "metric.rain": "Lluvia",
  "metric.irrig": "Riego",

  // --- units ---------------------------------------------------------------
  "unit.cells": "celdas",
  "unit.days": "días",
  "unit.density": "plantas/m²",
  "unit.perPlant": "g/planta",

  // --- timeline / tooltip --------------------------------------------------
  "day.label": "Día {0}",
  // Spanish writes day before month, so the placeholders swap places here.
  "date.format": "{1}/{0}",
  "tooltip.cell": "Día {0} · fila {1} / columna {2}",
  "tooltip.crop": "Cultivo",
  "tooltip.stage": "Etapa",
  "channel.cropLayout": "Distribución de cultivos",
  "na": "—",

  // --- bottom bar ----------------------------------------------------------
  "chart.hint": "Arrastra la línea de tiempo o haz clic en una curva para ver un día",
  "speed.label": "Velocidad",
  "speed.slow": "Lento",
  "speed.normal": "Normal",
  "speed.fast": "Rápido",
  "field.exaggerate": "Exageración de altura",

  // --- chart.ts ------------------------------------------------------------
  "chart.lai": "IAF",
  "chart.moisture": "Humedad",
  "chart.stress": "Estrés",
  "chart.rain": "Lluvia",
  "chart.dayFirst": "Día 1",
  "chart.dayLast": "Día {0}",
};
