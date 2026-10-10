# CONTRATO TIPADO DE ACCIONES Y OBSERVACIONES
**Herramienta:** `web-agent`  
**Módulos:** `web_agent.core.schemas`, `web_agent.browser.observe`, `web_agent.browser.actions`  
**Estado:** Especificación de Diseño Oficial (Reemplaza y extiende esquemas DOM iniciales)

---

## 1. Filosofía del Contrato
El modelo de inteligencia artificial se trata como un generador de propuestas **NO CONFIABLE**. Ninguna propiedad emitida por el modelo se pasa directamente a las APIs del navegador sin atravesar:
1. **Validación Sintáctica y Tipada (Pydantic `extra="forbid"`):** Rechazo de campos extraños, erratas o inyecciones de prompt.
2. **Validación Contextual y Temporal:** Verificación de que la acción hace referencia a la captura vigente mediante `observation_id` y que las coordenadas pertenecen estrictamente al área visible activa.
3. **Validación de Política de Seguridad (*Fail-Closed*):** Comprobación de que la operación está autorizada dentro del alcance de la tarea.

---

## 2. Contrato de Observación Estructurada (`Observation`)

El sistema abstrae la observación en un objeto fuertemente tipado. La interfaz del adaptador de inferencia se define formalmente como:

```python
async def propose_action(
    self, 
    context: TaskContext, 
    observation: VisualObservation | DOMObservation
) -> ProposedAction:
    ...
```

### 2.1. Esquema de Observación Visual (`VisualObservation`)
Es la estructura principal para la evaluación sin DOM:

```python
class VisualObservation(_Strict):
    observation_id: str = Field(..., max_length=64, description="ID único e irrepetible de esta captura")
    session_id: str = Field(..., max_length=64, description="Identificador de la sesión de navegador")
    captured_at: float = Field(..., description="Timestamp monotónico de captura")
    
    # Geometría en píxeles CSS (referencia para Playwright)
    viewport_width_css: int = Field(..., ge=320, le=3840)
    viewport_height_css: int = Field(..., ge=240, le=2160)
    
    # Dimensiones físicas de la imagen procesada
    image_width_px: int = Field(..., ge=320, le=7680)
    image_height_px: int = Field(..., ge=240, le=4320)
    
    # Referencia o payload
    image_reference: str = Field(..., description="URI interno o descriptor de la captura protegida")
    geometry_revision: int = Field(1, ge=1, description="Contador de cambios de tamaño/zoom del viewport")
    
    # Metadatos de seguridad
    is_redacted: bool = False
    crop_offset_x_css: Optional[int] = None
    crop_offset_y_css: Optional[int] = None
    crop_scale: float = 1.0
```

### 2.2. Vigencia y Caducidad de la Observación
Una observación queda **inmediatamente invalidada** ante cualquiera de estos eventos:
- Cambio de tamaño del viewport o resize de la ventana del navegador.
- Navegación a una nueva URL o redirección HTTP.
- Desplazamiento de scroll significativo.
- Transcurso de más de **15 segundos** desde `captured_at` sin ejecutar la acción propuesta.

Si el `observation_id` recibido en una `ProposedAction` no coincide con la observación activa, el runner descarta la acción y fuerza un nuevo ciclo de observación.

---

## 3. Contrato de Acciones Propuestas (`ProposedAction`)

### 3.1. Catálogo Cerrado de Acciones

```python
class ActionType(str, Enum):
    # Acciones Visuales (Nivel 5 - Modo Principal)
    CLICK_COORDINATES = "click_coordinates"
    DOUBLE_CLICK_COORDINATES = "double_click_coordinates"
    RIGHT_CLICK_COORDINATES = "right_click_coordinates"
    TYPE_AT_COORDINATES = "type_at_coordinates"
    KEYBOARD_HOTKEY = "keyboard_hotkey"
    SCROLL_COORDINATES = "scroll_coordinates"
    WAIT = "wait"
    
    # Acciones Estructurales (Modo DOM - Secundario)
    NAVIGATE = "navigate"
    CLICK_DOM = "click_dom"
    FILL_DOM = "fill_dom"
    
    # Control de Flujo de Tarea
    DONE = "done"
    ASK_HUMAN = "ask_human"
```

### 3.2. Modelo de Validación Cruzada (Pydantic)

```python
class ProposedAction(_Strict):
    action_type: ActionType
    observation_id: str = Field(..., min_length=1, max_length=64, description="Vínculo obligatorio con la captura analizada")
    
    # Coordenadas normalizadas [0..1000] para acciones espaciales
    coord_x: Optional[int] = Field(None, ge=0, le=1000)
    coord_y: Optional[int] = Field(None, ge=0, le=1000)
    
    # Parámetros para scroll
    delta_x: Optional[int] = Field(None, ge=-2000, le=2000)
    delta_y: Optional[int] = Field(None, ge=-2000, le=2000)
    
    # Parámetros para texto y teclado
    value: Optional[str] = Field(None, max_length=2000, description="Texto literal o clave de secreto")
    is_secret_ref: bool = False
    clear_before_type: bool = True
    key_sequence: Optional[str] = Field(None, max_length=50, description="Tecla o atajo ej. 'Enter', 'Escape', 'Control+A'")
    
    # Parámetros para espera y control
    duration_seconds: Optional[float] = Field(None, ge=0.1, le=30.0)
    reasoning: str = Field(..., min_length=1, max_length=2000)
    
    # Referencias DOM secundarias
    target_element_id: Optional[str] = Field(None, pattern=r"^e\d{1,5}$")
    url: Optional[str] = Field(None, max_length=2048)

    @model_validator(mode="after")
    def validate_action_consistency(self) -> "ProposedAction":
        t = self.action_type
        
        # 1. Validaciones espaciales (clic y tipeo)
        if t in (ActionType.CLICK_COORDINATES, ActionType.DOUBLE_CLICK_COORDINATES, 
                 ActionType.RIGHT_CLICK_COORDINATES, ActionType.TYPE_AT_COORDINATES):
            if self.coord_x is None or self.coord_y is None:
                raise ValueError(f"Acción {t} requiere obligatoriamente coord_x y coord_y.")
            if self.url is not None:
                raise ValueError(f"Acción espacial {t} no admite parámetro 'url'.")
                
        # 2. Validación de escritura
        if t == ActionType.TYPE_AT_COORDINATES:
            if not self.value:
                raise ValueError("type_at_coordinates requiere un valor de texto o referencia de secreto.")
                
        # 3. Validación de atajos de teclado
        if t == ActionType.KEYBOARD_HOTKEY:
            if not self.key_sequence:
                raise ValueError("keyboard_hotkey requiere definir key_sequence.")
                
        # 4. Validación de espera
        if t == ActionType.WAIT:
            if self.duration_seconds is None:
                raise ValueError("wait requiere duration_seconds.")
                
        # 5. Validación de navegación
        if t == ActionType.NAVIGATE:
            if not self.url:
                raise ValueError("navigate requiere 'url'.")
                
        # 6. Acción DONE
        if t == ActionType.DONE:
            # DONE solo solicita evaluación de fin de tarea; no debe llevar coordenadas
            if self.coord_x is not None or self.coord_y is not None:
                raise ValueError("done no admite coordenadas de interacción.")
                
        return self
```

---

## 4. Transformación Matemática a Píxeles CSS de Viewport

Playwright ejecuta sus métodos de ratón (`page.mouse.click`, `move`) tomando como entrada **píxeles CSS del viewport**, no coordenadas físicas ni subpíxeles arbitrarios.

Dada una coordenada normalizada $(x_{norm}, y_{norm}) \in [0, 1000]^2$ y un viewport con dimensiones $W_{css}$ y $H_{css}$:

$$X_{css} = \operatorname{round}\left(\frac{x_{norm}}{1000} \cdot (W_{css} - 1)\right)$$

$$Y_{css} = \operatorname{round}\left(\frac{y_{norm}}{1000} \cdot (H_{css} - 1)\right)$$

### Invariantes Estrictos de Frontera (*Boundary Check*):
Antes de despachar la acción a Playwright, el ejecutor evalúa:
$$0 \le X_{css} < W_{css} \quad \land \quad 0 \le Y_{css} < H_{css}$$

*Justificación Técnica:* Un valor de $X_{css} = W_{css}$ queda exactamente en el borde exterior no indexable del viewport. Multiplicar por $(W_{css} - 1)$ garantiza que para $x_{norm} = 1000$, el píxel resultante sea el último píxel visible de la ventana ($W_{css}-1$).
