# ARQUITECTURA TÉCNICA: NIVEL 5 — OPERACIÓN VISUAL Y GUI PURA / CANVAS
**Herramienta:** `web-agent`  
**Módulos:** `web_agent.vision`, `web_agent.browser.actions`, `web_agent.core.runner`  
**Estado:** Modo Principal de Operación para la Hackathon (ADR-001)

---

## 1. El Escenario de Operación Principal
El reto exige que el agente opere sistemas legacy de hace 15 años accedidos mediante escritorio remoto transmitido por navegador (noVNC, Guacamole o canvas puro):
- **Ausencia de Árbol DOM de la Aplicación:** El DOM del navegador anfitrión únicamente contiene el contenedor de transmisión (ej. `<canvas id="noVNC_canvas">`). Los botones, formularios, menús y grillas de la aplicación remota existen **exclusivamente como píxeles**.
- **Percepción Visual Primaria:** El agente debe observar la pantalla, interpretar visualmente controles gráficos tradicionales (estilo Win32, Java Swing, Visual Basic) y emitir eventos analógicos de ratón y teclado mediante coordenadas del viewport.

---

## 2. Modos de Percepción y Configuración `visual_only`

El sistema define tres modos de operación explícitos:

| Modo | Comportamiento del Agente |
| :--- | :--- |
| **`visual_only`** (Modo Principal) | **Capturas del viewport y eventos de ratón/teclado.** No inspecciona el DOM de la aplicación para identificar controles. Si las reglas prohíben inspeccionar el HTML del cliente remoto, la autenticación y operación se realizan 100% de forma visual. |
| **`dom`** (Modo Secundario) | Observación estructural HTML y localizadores accesibles cuando las reglas permitan el uso de DOM. |
| **`auto`** (Modo Híbrido) | Selección heurística cuando las reglas permitan ambos modos. *Nota de diseño:* Pocos elementos DOM o una URL con "vnc" son meros indicios, no pruebas concluyentes. |

Para la evaluación del reto, el agente se inicializa en **`visual_only`**, garantizando que no gaste tiempo ni dependa de inspecciones DOM que no reflejan la aplicación interna.

---

## 3. Modelo Matemático de Geometría y Coordenadas

### 3.1. Cuadrícula Normalizada Universal
El modelo multimodal (VLM) propone coordenadas en el espacio entero:
$$x_{norm} \in [0, 1000], \quad y_{norm} \in [0, 1000]$$

### 3.2. Transformación a Píxeles CSS de Viewport
Playwright recibe coordenadas en **píxeles CSS** del viewport activo.

Para una captura completa del viewport con dimensiones $W_{css}$ y $H_{css}$:
$$X_{css} = \operatorname{round}\left(\frac{x_{norm}}{1000} \cdot (W_{css} - 1)\right)$$
$$Y_{css} = \operatorname{round}\left(\frac{y_{norm}}{1000} \cdot (H_{css} - 1)\right)$$

### 3.3. Invariante de Frontera (*Boundary Check*)
Para evitar errores de índice donde $X = W$ o $Y = H$ queden fuera de la superficie indexable:
$$0 \le X_{css} < W_{css} \quad \land \quad 0 \le Y_{css} < H_{css}$$

Si la imagen enviada al modelo requiriese recorte (*crop*) por motivos de resolución, la observación registrará el offset $(O_x, O_y)$ y la escala $S$ para reconstruir con exactitud el píxel final en el viewport. Para el MVP de evaluación se utilizan capturas completas del viewport.

---

## 4. Vigencia de Observaciones y Manejo de Latencia

1. **Vínculo Obligatorio (`observation_id`):** Toda acción espacial (`click_coordinates`, `type_at_coordinates`) debe enviar el `observation_id` de la captura sobre la que calculó sus coordenadas.
2. **Invalidación Automática:** Si la página sufre redimensionamiento, scroll, navegación o transcurren más de 15 segundos antes de emitir la acción, la propuesta es rechazada y el agente reobserva.
3. **Transporte de Escritorio Autorizado:** La sesión de Playwright autoriza las conexiones WebSocket dirigidas al host y puerto del escritorio de evaluación, evitando cierres indiscriminados de conexión que impidan la transmisión de video del escritorio.

---

## 5. Emulación Determinista de Periféricos (Playwright)

### Clics de Precisión
```python
async def execute_click(page: Page, x_css: int, y_css: int, dbl: bool = False, right: bool = False):
    button = "right" if right else "left"
    click_count = 2 if dbl else 1
    
    # Mover el ratón para activar estados visuales si aplica
    await page.mouse.move(x_css, y_css)
    await asyncio.sleep(0.05)
    await page.mouse.click(x_css, y_css, button=button, click_count=click_count)
```

### Escritura Controlada en Campos de Canvas
```python
async def execute_type(page: Page, x_css: int, y_css: int, text: str, replace: bool = True):
    # 1. Ganar foco en el campo
    await page.mouse.click(x_css, y_css)
    await asyncio.sleep(0.1)
    
    # 2. Reemplazo seguro si se solicita
    if replace:
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.05)
        
    # 3. Tipeo con cadencia controlada (20 ms) para buffers de escritorio remoto
    await page.keyboard.type(text, delay=20)
```
