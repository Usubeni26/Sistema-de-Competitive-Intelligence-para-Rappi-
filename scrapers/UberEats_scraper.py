import getpass
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from scrapers.base_scraper import BaseScraper


class UberEats_scraper(BaseScraper):
    """
    Scraper para UberEats México.
    - Abre UberEats y cierra popups
    - Setea la dirección desde el JSON
    - Busca el campo "order" del JSON en el buscador global
    - Selecciona el resultado del restaurante que coincida textualmente con la búsqueda
    - Dentro del restaurante, busca el producto que coincida con "order" y lo agrega al carrito
    - Si se abre un modal de personalización:
        * Selecciona la primera opción de cada grupo de radio buttons
        * EXCEPTO en el grupo de bebida: selecciona la opción cuyo texto contenga "retail" del JSON
    - Extrae información básica visible
    - Guarda tiempo_envio, costo_envio, costo_total y costo_retail en raw_YYYYMMDD.json

    """

    # ------------------------------------------------------------------
    # Credenciales: se solicitan UNA SOLA VEZ al instanciar el scraper.
    # Se guardan como variable de clase para reutilizarlas entre instancias
    # de la misma ejecución sin volver a preguntar.
    # ------------------------------------------------------------------
    _credentials: Optional[Dict[str, str]] = None

    def __init__(self, **kwargs):
        super().__init__(platform_name="ubereats", **kwargs)
        self.base_url = "https://www.ubereats.com/mx"
        self._ensure_credentials()

    @classmethod
    def _ensure_credentials(cls) -> None:
        """
        Solicita correo y contraseña por consola si aún no se han capturado.
        Usa getpass para que la contraseña NO se muestre en pantalla.
        """
        if cls._credentials is not None:
            return  # Ya fueron ingresadas en esta sesión

        print("\n─── Inicio de sesión en UberEats ───────────────────────────")
        print("Las credenciales se solicitan una sola vez por ejecución.\n")

        email = input("  Correo electrónico: ").strip()
        if not email:
            print("[ERROR] El correo no puede estar vacío.", file=sys.stderr)
            sys.exit(1)

        password = getpass.getpass("  Contraseña:         ")
        if not password:
            print("[ERROR] La contraseña no puede estar vacía.", file=sys.stderr)
            sys.exit(1)

        cls._credentials = {"email": email, "password": password}
        print("────────────────────────────────────────────────────────────\n")

    async def scrape_address(self, address_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Scrapea una dirección. Busca el campo "order" del JSON.
        Retorna los mismos parámetros que RappiScraper:
        tiempo_envio, costo_envio, costo_total, costo_retail.
        """
        results: List[Dict[str, Any]] = []

        address_id = address_data["id"]
        city = address_data["city"]
        zone_name = address_data["zone_name"]
        zone_type = address_data["zone_type"]

        # --- NORMALIZACIÓN: order puede ser lista o string ---
        raw_order = address_data.get("order", "")
        if isinstance(raw_order, list):
            order = raw_order[0] if raw_order else ""
            self.logger.info(f"[ubereats] order era una lista, se toma el primer elemento: '{order}'")
        else:
            order = raw_order
        # ------------------------------------------------------

        # --- NORMALIZACIÓN: retail puede ser lista o string ---
        raw_retail = address_data.get("retail", "")
        if isinstance(raw_retail, list):
            retail = raw_retail[0] if raw_retail else ""
            self.logger.info(f"[ubereats] retail era una lista, se toma el primer elemento: '{retail}'")
        else:
            retail = raw_retail
        # ------------------------------------------------------

        self.logger.info(f"[ubereats] Iniciando scrape para {address_id} - {zone_name}")
        self.logger.info(f"[ubereats] order='{order}'")
        self.logger.info(f"[ubereats] retail='{retail}'")

        await self.retry(self._open_homepage)
        await self.save_screenshot(f"{address_id}_home")

        await self._try_set_address(address_data)
        await self.save_screenshot(f"{address_id}_after_address")

        # ── LOGIN: se ejecuta después de fijar la dirección y antes de buscar ──
        await self.retry(self._login)
        await self.save_screenshot(f"{address_id}_after_login")
        # ───────────────────────────────────────────────────────────────────────

        if not order:
            self.logger.warning(f"[ubereats] El campo 'order' está vacío para {address_id}.")
            return results

        try:
            result = await self.retry(
                self._scrape_restaurant_search_result,
                address_data,
                order,
                retail,
            )
            results.append(result)
        except Exception as e:
            self.logger.warning(f"[ubereats] Error scrapeando order '{order}' en {address_id}: {e}")
            results.append(
                self.build_result(
                    address_id=address_id,
                    city=city,
                    zone_name=zone_name,
                    zone_type=zone_type,
                    restaurant=order,
                    available=False,
                    raw_metadata={"error": str(e)},
                )
            )

        return results

    async def _open_homepage(self) -> None:
        await self.safe_goto(self.base_url)
        await self.page.wait_for_selector("body", timeout=10000)
        await self._dismiss_popups()
        await self.random_delay(1.0, 2.0)

    async def _dismiss_popups(self) -> None:
        """
        Cierra popups comunes de UberEats: cookies, ubicación, notificaciones, etc.
        UberEats suele mostrar un popup de cookies y uno de ubicación al inicio.
        """
        possible_buttons = [
            # Cookies y aceptar
            "button:has-text('Aceptar')",
            "button:has-text('Aceptar todo')",
            "button:has-text('Aceptar cookies')",
            "button:has-text('Aceptar y continuar')",
            # Cerrar genérico
            "button:has-text('Cerrar')",
            "button:has-text('Ahora no')",
            "button:has-text('No, gracias')",
            # Notificaciones
            "button:has-text('Bloquear')",
            # Atributos ARIA y testid comunes en UberEats
            "button[aria-label='Close']",
            "button[aria-label='Cerrar']",
            "[data-testid='close-button']",
            "[data-testid='modal-close-button']",
        ]
        for selector in possible_buttons:
            clicked = await self.click_if_exists(selector)
            if clicked:
                self.logger.info(f"[ubereats] Popup cerrado con selector: {selector}")
                await self.random_delay(0.3, 0.6)

    async def _login(self) -> None:
        """
        Inicia sesión en UberEats usando las credenciales capturadas en consola.

        Flujo:
          1. Detecta si ya está logueado (presencia de avatar / menú de usuario).
          2. Si no, hace clic en el botón "Iniciar sesión" del header.
          3. Espera a que se cargue la página de auth de Uber.
          4. Ingresa correo → Continuar → contraseña → Iniciar sesión.
          5. Espera confirmación de sesión activa.
        """
        if not self.page:
            return

        # ── 0. Verificar si ya hay sesión activa ─────────────────────────────
        already_logged = await self._is_logged_in()
        if already_logged:
            self.logger.info("[ubereats][login] Sesión ya activa, se omite el login.")
            return

        self.logger.info("[ubereats][login] Iniciando proceso de login...")

        # ── 1. Hacer clic en el botón "Iniciar sesión" del header ────────────
        sign_in_selectors = [
            "a[data-test='header-sign-in']",
            "a:has-text('Iniciar sesión')",
            "button:has-text('Iniciar sesión')",
            "[data-test='header-sign-in']",
        ]
        clicked = False
        for selector in sign_in_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=5000)
                    self.logger.info(f"[ubereats][login] Botón de login clicado: {selector}")
                    clicked = True
                    break
            except Exception as e:
                self.logger.debug(f"[ubereats][login] Selector '{selector}' falló: {e}")

        if not clicked:
            self.logger.warning("[ubereats][login] No se encontró el botón 'Iniciar sesión'.")
            await self._diagnose_page_inputs()
            return

        # ── 2. Esperar la página de auth de Uber ─────────────────────────────
        try:
            await self.page.wait_for_url("**/v2/**", timeout=1000)
        except Exception:
            # Puede que la URL cambie con otro patrón; continuar de todas formas
            pass
        await self.random_delay(1.5, 2.5)

        # ── 3. Ingresar correo electrónico ────────────────────────────────────
        email = self._credentials["email"]          # type: ignore[index]
        password = self._credentials["password"]    # type: ignore[index]

        email_selectors = [
            "input[name='email']",
            "input[type='email']",
            "input[autocomplete='email']",
            "input[autocomplete='username']",
            "input[placeholder*='correo']",
            "input[placeholder*='email']",
        ]
        email_input = None
        for selector in email_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    email_input = locator
                    self.logger.info(f"[ubereats][login] Input de correo encontrado: {selector}")
                    break
            except Exception:
                continue

        if not email_input:
            self.logger.error("[ubereats][login] No se encontró el campo de correo.")
            await self._diagnose_page_inputs()
            return

        await email_input.click()
        await email_input.fill(email)
        await self.random_delay(0.5, 1.0)

        # ── 4. Clic en "Continuar" / "Next" ──────────────────────────────────
        continue_selectors = [
            "button:has-text('Continuar')",
            "button:has-text('Continue')",
            "button[type='submit']",
        ]
        for selector in continue_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=5000)
                    self.logger.info(f"[ubereats][login] 'Continuar' clicado: {selector}")
                    break
            except Exception:
                continue

        await self.random_delay(1.5, 2.5)

        # ── 5. Detectar flujo: ¿contraseña directa o PIN por correo? ─────────
        #
        # UberEats a veces pide la contraseña de inmediato; otras veces envía
        # un PIN al correo. En el segundo caso aparece la pantalla del PIN y
        # hay que navegar: "Más opciones" → opción "Contraseña".
        #
        password_input = await self._find_password_input()

        if not password_input:
            self.logger.info(
                "[ubereats][login] Campo de contraseña no visible. "
                "Verificando si hay pantalla de PIN..."
            )
            password_input = await self._switch_to_password_flow()

        if not password_input:
            self.logger.error(
                "[ubereats][login] No se pudo acceder al campo de contraseña "
                "ni a través del flujo de PIN."
            )
            await self._diagnose_page_inputs()
            return

        await password_input.click()
        await password_input.fill(password)
        await self.random_delay(0.5, 1.0)

        # ── 6. Clic en "Iniciar sesión" / "Log in" ───────────────────────────
        submit_selectors = [
            "button:has-text('Iniciar sesión')",
            "button:has-text('Log in')",
            "button:has-text('Sign in')",
            "button[type='submit']",
        ]
        for selector in submit_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=5000)
                    self.logger.info(f"[ubereats][login] Submit de login clicado: {selector}")
                    break
            except Exception:
                continue

        await self.random_delay(2.0, 3.0)

        # ── 7. Detectar pantalla de "clave de acceso" (passkey) ─────────────
        #
        # Tras ingresar la contraseña, UberEats puede mostrar una pantalla de
        # passkey. Se intenta cancelar con botones; si no se puede, se navega
     
        _PASSKEY_FALLBACK_URL = "https://www.ubereats.com/mx/category-feed/Shop?pl=JTdCJTIyYWRkcmVzcyUyMiUzQSUyMlVuaXZlcnNpZGFkJTIwU2VyZ2lvJTIwQXJib2xlZGElMjIlMkMlMjJyZWZlcmVuY2UlMjIlM0ElMjJDaElKc1N4My1sdWFQNDRSakNDTWN6MU1DM1UlMjIlMkMlMjJyZWZlcmVuY2VUeXBlJTIyJTNBJTIyZ29vZ2xlX3BsYWNlcyUyMiUyQyUyMmxhdGl0dWRlJTIyJTNBNC42NjA3MzAwOTk5OTk5OTklMkMlMjJsb25naXR1ZGUlMjIlM0EtNzQuMDU5NzE3OCU3RA%3D%3D"

        passkey_cancelled = await self._dismiss_passkey_screen()
        if passkey_cancelled:
            self.logger.warning(
                "[ubereats][login] ⚠️  Pantalla de 'clave de acceso' detectada."
            )
            await self.random_delay(1.0, 1.5)

        # ── 8. Esperar redirección de vuelta a UberEats ───────────────────────
        try:
            await self.page.wait_for_url("**/ubereats.com/**", timeout=10000)
        except Exception:
            pass
        await self.random_delay(1.0, 1.5)

        
        current_url = self.page.url
        if "ubereats.com" not in current_url:
            self.logger.warning(
                f"[ubereats][login] Sigue fuera de UberEats ({current_url}). "
                "Navegando directo a la URL de feed con dirección..."
            )
            try:
                await self.safe_goto(_PASSKEY_FALLBACK_URL)
                await self.page.wait_for_selector("body", timeout=10000)
                await self.random_delay(1.5, 2.5)
                self.logger.info(
                    f"[ubereats][login] Navegación directa exitosa: {self.page.url}"
                )
            except Exception as e:
                self.logger.error(
                    f"[ubereats][login] Error al navegar a URL de fallback: {e}"
                )

        await self._dismiss_popups()

        # ── 9. Confirmar login ────────────────────────────────────────────────
        if await self._is_logged_in():
            self.logger.info("[ubereats][login] ✓ Login exitoso.")
        else:
            self.logger.warning(
                "[ubereats][login] No se pudo confirmar el login. "
                "Es posible que haya un captcha o verificación adicional."
            )

    async def _dismiss_passkey_screen(self) -> bool:
        """
        Detecta si UberEats está mostrando la pantalla de "clave de acceso"
        (passkey) y la cancela haciendo clic en el botón correspondiente.

        Retorna True si se detectó y canceló la pantalla, False en caso contrario.

        Indicadores de la pantalla de passkey que se buscan:
          - Texto "clave de acceso" en la página.
          - Botones con texto "Ahora no", "Cancelar", "Omitir", "Not now",
            "Maybe later", "Skip" o "Cancel".
        """
        if not self.page:
            return False

        # ── 1. Verificar si hay indicios de la pantalla de passkey ───────────
        passkey_indicators = [
            "text='clave de acceso'",
            "text='passkey'",
            "text='Clave de acceso'",
            "text='Passkey'",
            ":has-text('clave de acceso')",
            ":has-text('Iniciar sesión más rápido')",   # título habitual del modal
            ":has-text('usar una clave de acceso')",
        ]
        passkey_detected = False
        for indicator in passkey_indicators:
            try:
                locator = self.page.locator(indicator).first
                if await locator.count() > 0 and await locator.is_visible():
                    self.logger.info(
                        f"[ubereats][login][passkey] Pantalla de passkey detectada "
                        f"con indicador: {indicator}"
                    )
                    passkey_detected = True
                    break
            except Exception:
                continue

        if not passkey_detected:
            return False

        # ── 2. Cancelar la pantalla ───────────────────────────────────────────
        cancel_selectors = [
            "button:has-text('Ahora no')",
            "button:has-text('Cancelar')",
            "button:has-text('Omitir')",
            "button:has-text('Not now')",
            "button:has-text('Maybe later')",
            "button:has-text('Skip')",
            "button:has-text('Cancel')",
            "button[data-testid='cancel']",
            "button[data-testid='skip']",
            "a:has-text('Ahora no')",
            "a:has-text('Cancelar')",
        ]
        for selector in cancel_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=5000)
                    self.logger.info(
                        f"[ubereats][login][passkey] Pantalla cancelada con: {selector}"
                    )
                    return True
            except Exception as e:
                self.logger.debug(
                    f"[ubereats][login][passkey] Selector de cancelación '{selector}' falló: {e}"
                )

        # Si se detectó pero no se pudo cancelar con ningún botón, informar igualmente
        self.logger.warning(
            "[ubereats][login][passkey] Se detectó la pantalla de passkey "
            "pero no se encontró un botón para cancelarla. "
            "Puede requerir intervención manual."
        )
        return True  # Se detectó aunque no se canceló con éxito

    async def _find_password_input(self):
        """
        Busca el input de contraseña en la pantalla actual.
        Retorna el locator si lo encuentra visible, o None si no está.
        """
        password_selectors = [
            "input[name='password']",
            "input[type='password']",
            "input[autocomplete='current-password']",
        ]
        for selector in password_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    self.logger.info(f"[ubereats][login] Input de contraseña encontrado: {selector}")
                    return locator
            except Exception:
                continue
        return None

    async def _switch_to_password_flow(self):
        """
        Maneja el caso en que UberEats muestra la pantalla de PIN por correo
        en lugar del campo de contraseña.

        Flujo:
          1. Clic en el botón "Más opciones"  (data-testid="Más opciones").
          2. Clic en la opción "Contraseña"   (p con texto "Contraseña").
          3. Espera y retorna el input de contraseña, o None si no aparece.
        """
        if not self.page:
            return None

        # ── 1. Clic en "Más opciones" ─────────────────────────────────────
        mas_opciones_selectors = [
            "button[data-testid='Más opciones']",
            "button[id='alt-action-help-v2']",
            "button:has-text('Más opciones')",
        ]
        clicked_mas = False
        for selector in mas_opciones_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=5000)
                    self.logger.info(
                        f"[ubereats][login] 'Más opciones' clicado: {selector}"
                    )
                    clicked_mas = True
                    break
            except Exception as e:
                self.logger.debug(
                    f"[ubereats][login] Selector 'Más opciones' '{selector}' falló: {e}"
                )

        if not clicked_mas:
            self.logger.warning(
                "[ubereats][login] No se encontró el botón 'Más opciones'. "
                "Puede que no haya pantalla de PIN o el selector cambió."
            )
            return None

        await self.random_delay(1.0, 1.5)

        # ── 2. Clic en la opción "Contraseña" ────────────────────────────
        contrasena_selectors = [
            # El elemento exacto del HTML proporcionado
            "p:has-text('Contraseña')",
            "div:has-text('Contraseña') p",
            # Fallbacks más amplios
            "[role='option']:has-text('Contraseña')",
            "li:has-text('Contraseña')",
            "button:has-text('Contraseña')",
        ]
        clicked_pwd = False
        for selector in contrasena_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    await locator.click(timeout=5000)
                    self.logger.info(
                        f"[ubereats][login] Opción 'Contraseña' clicada: {selector}"
                    )
                    clicked_pwd = True
                    break
            except Exception as e:
                self.logger.debug(
                    f"[ubereats][login] Selector 'Contraseña' '{selector}' falló: {e}"
                )

        if not clicked_pwd:
            self.logger.warning(
                "[ubereats][login] No se encontró la opción 'Contraseña' "
                "en el menú de 'Más opciones'."
            )
            return None

        # ── 3. Esperar y retornar el input de contraseña ─────────────────
        await self.random_delay(1.5, 2.0)
        return await self._find_password_input()

    async def _is_logged_in(self) -> bool:
        """
        Detecta si ya hay una sesión activa comprobando la ausencia del botón
        'Iniciar sesión' y/o la presencia de elementos del perfil del usuario.
        """
        if not self.page:
            return False
        # Si seguimos en auth.uber.com, definitivamente NO estamos logueados
        if "auth.uber.com" in self.page.url:
            return False
        try:
            # Si el botón de login ya no existe, asumimos sesión activa
            sign_in_btn = self.page.locator("a[data-test='header-sign-in']").first
            count = await sign_in_btn.count()
            if count == 0:
                return True
            # También podría estar oculto tras login
            if not await sign_in_btn.is_visible():
                return True
        except Exception:
            pass
        return False

    async def _diagnose_page_inputs(self) -> None:
        if not self.page:
            return
        try:
            info = await self.page.evaluate("""() => {
                const results = { buttons: [], inputs: [] };
                document.querySelectorAll('button, [role="button"], a[role="button"]').forEach(el => {
                    if (el.offsetParent !== null) {
                        results.buttons.push({
                            tag: el.tagName,
                            text: (el.innerText || '').trim().slice(0, 80),
                            testid: el.getAttribute('data-testid') || '',
                            ariaLabel: el.getAttribute('aria-label') || '',
                            classes: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
                        });
                    }
                });
                document.querySelectorAll('input').forEach(el => {
                    results.inputs.push({
                        type: el.type,
                        placeholder: el.placeholder || '',
                        name: el.name || '',
                        id: el.id || '',
                        autocomplete: el.getAttribute('autocomplete') || '',
                        testid: el.getAttribute('data-testid') || '',
                        classes: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
                        visible: el.offsetParent !== null,
                    });
                });
                return results;
            }""")
            self.logger.info(f"[ubereats][diagnóstico] Botones visibles: {info['buttons']}")
            self.logger.info(f"[ubereats][diagnóstico] Inputs en DOM: {info['inputs']}")
        except Exception as e:
            self.logger.warning(f"[ubereats][diagnóstico] Error al inspeccionar DOM: {e}")

    async def _try_set_address(self, address_data: Dict[str, Any]) -> bool:
        """
        Setea la dirección en UberEats.

        Flujo real de la página:
          1. Al cargar la home, el input de dirección ya está visible directamente
             (id="location-typeahead-home-input", name="searchTerm",
              placeholder="Ingresa la dirección de entrega").
          2. Se escribe la dirección y se selecciona la primera sugerencia del autocomplete.
          3. Se hace clic en el botón "Buscar comida" para confirmar.
        """
        if not self.page:
            return False

        address = address_data["address"]
        self.logger.info(f"[ubereats] Intentando setear dirección: {address}")

        # ── 1. Localizar el input de dirección ───────────────────────────────────

        input_element = None
        input_selectors = [
            "input#location-typeahead-home-input",          # ID exacto del HTML
            "input[name='searchTerm']",                     # name estable
            "input[placeholder='Ingresa la dirección de entrega']",
            "input[role='combobox'][aria-autocomplete='list']",
            "input[placeholder*='dirección de entrega']",
            "input[id*='location-typeahead']",
            "input[placeholder*='dirección']",
            "input[type='text']",
        ]

        for selector in input_selectors:
            try:
                candidate = self.page.locator(selector).first
                if await candidate.count() > 0 and await candidate.is_visible():
                    input_element = candidate
                    self.logger.info(f"[ubereats] Input de dirección encontrado: {selector}")
                    break
            except Exception as e:
                self.logger.debug(f"[ubereats] Falló selector de input '{selector}': {e}")

        if not input_element:
            self.logger.error("[ubereats] No se encontró el input de dirección.")
            await self._diagnose_page_inputs()
            return False

        # ── 2. Escribir la dirección 
        try:
            await input_element.scroll_into_view_if_needed()
            await input_element.click()
            await input_element.fill("")
            await input_element.type(address, delay=60)
            self.logger.info(f"[ubereats] Dirección escrita: '{address}'")
        except Exception as e:
            self.logger.error(f"[ubereats] Error al escribir dirección: {e}")
            return False

        await self.random_delay(1.0, 2.0)

        # ── 3. Seleccionar la primera sugerencia del autocomplete
        
        selected = False
        suggestion_selectors = [
            "#location-typeahead-home-menu [role='option']",
            "#location-typeahead-home-menu li",
            "[role='listbox'] [role='option']",
            "[role='option']",
            "li[role='option']",
            "ul li",
        ]

        for selector in suggestion_selectors:
            try:
                await self.page.wait_for_selector(selector, state="visible", timeout=4000)
                suggestions = await self.page.query_selector_all(selector)
                if suggestions:
                    await suggestions[0].click()
                    self.logger.info(f"[ubereats] Primera sugerencia seleccionada: {selector}")
                    selected = True
                    break
            except Exception:
                continue

        if not selected:
            self.logger.warning("[ubereats] No aparecieron sugerencias. Usando Enter como fallback.")
            try:
                await input_element.press("Enter")
            except Exception:
                pass

        await self.random_delay(0.5, 1.0)

         # 4. Clic en "Buscar comida" 
        confirm_clicked = False
        confirm_selectors = [
            "button:has-text('Buscar comida')",   # texto exacto observado en el HTML
            "button:has-text('Buscar')",
            "button[type='submit']",
        ]

        for selector in confirm_selectors:
            try:
                await self.page.wait_for_selector(selector, state="visible", timeout=5000)
                locator = self.page.locator(selector).first
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed()
                    await locator.click(timeout=3000)
                    self.logger.info(f"[ubereats] Botón de confirmación clicado: {selector}")
                    confirm_clicked = True
                    break
            except Exception:
                continue

        if not confirm_clicked:
            self.logger.warning("[ubereats] No se encontró el botón 'Buscar comida'.")

        await self.random_delay(1.5, 2.5)

        # ── 5. Cerrar popups que puedan aparecer tras la navegación ───────────────
        await self._dismiss_popups()

        return confirm_clicked or selected

    # --------------------------------------------------------------------------
    # BÚSQUEDA DE RESTAURANTE
    # --------------------------------------------------------------------------

    async def _try_search_restaurant(self, query: str) -> bool:
        """
        Busca el restaurante en UberEats usando el buscador global.
        UberEats tiene una barra de búsqueda en la parte superior de la página de inicio.
        Escribe la query, espera resultados y selecciona la coincidencia textual.
        """
        if not self.page:
            return False

        self.logger.info(f"[ubereats] Buscando restaurante: '{query}'")

        # ── 1. Abrir/enfocar el campo de búsqueda ─────────────────────────────
        search_input = None
        search_selectors = [
            "input[data-testid='search-input']",
            "input[data-testid='location-search-input']",
            "input[placeholder*='Buscar']",
            "input[placeholder*='buscar']",
            "input[placeholder*='restaurantes']",
            "input[aria-label*='Buscar']",
            "input[aria-label*='buscar']",
            "input[type='search']",
            # El ícono de lupa que abre el campo de búsqueda
            "button[data-testid='search-button']",
            "a[href*='/search']",
            "button[aria-label*='Buscar']",
        ]

        for selector in search_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0:
                    await locator.click(timeout=3000)
                    await self.random_delay(0.5, 1.0)
                    # Si era un botón que abre el input, buscar el input nuevamente
                    tag = await locator.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "input":
                        search_input = locator
                        self.logger.info(f"[ubereats] Input de búsqueda encontrado: {selector}")
                    else:
                        # Es un botón/ícono: buscar el input que se abrió
                        for input_sel in ["input[type='search']", "input[type='text']", "input[data-testid*='search']"]:
                            try:
                                inp = self.page.locator(input_sel).first
                                if await inp.count() > 0 and await inp.is_visible():
                                    search_input = inp
                                    self.logger.info(f"[ubereats] Input post-click encontrado: {input_sel}")
                                    break
                            except Exception:
                                continue
                    if search_input:
                        break
            except Exception as e:
                self.logger.debug(f"[ubereats] Falló selector de búsqueda '{selector}': {e}")

        if not search_input:
            self.logger.warning("[ubereats] No se encontró campo de búsqueda.")
            return False

        # ── 2. Escribir la búsqueda ───────────────────────────────────────────
        try:
            await search_input.fill("")
            await search_input.type(query, delay=60)
            self.logger.info(f"[ubereats] Query escrita en buscador: '{query}'")
        except Exception as e:
            self.logger.warning(f"[ubereats] Error escribiendo query: {e}")
            return False

        await self.random_delay(1.5, 2.5)

        # ── 3. Seleccionar resultado coincidente ──────────────────────────────
        normalized_query = self._normalize_text(query)

        result_selectors = [
            # Tarjetas de restaurantes en resultados de búsqueda
            "[data-testid='store-card']",
            "[data-testid='store-title']",
            "[data-testid='rich-text']",
            "h3",
            "h2",
            # Sugerencias del autocomplete
            "[data-testid='search-suggestion']",
            "[role='option']",
            "li[role='option']",
        ]

        exact_match = None
        partial_match = None

        # Esperar a que aparezcan resultados
        for selector in result_selectors:
            try:
                await self.page.wait_for_selector(selector, state="visible", timeout=5000)
                break
            except Exception:
                continue

        for selector in result_selectors:
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
                if count == 0:
                    continue

                self.logger.info(f"[ubereats] Evaluando {count} candidatos con selector: {selector}")

                for i in range(count):
                    try:
                        el = locator.nth(i)
                        if not await el.is_visible():
                            continue
                        text = (await el.inner_text()).strip()
                        if not text:
                            continue

                        normalized_text = self._normalize_text(text)

                        if normalized_text == normalized_query:
                            exact_match = el
                            self.logger.info(f"[ubereats] Coincidencia EXACTA encontrada: '{text}'")
                            break

                        if normalized_query in normalized_text:
                            if partial_match is None:
                                partial_match = el
                                self.logger.info(f"[ubereats] Coincidencia PARCIAL candidata: '{text}'")

                    except Exception:
                        continue

                if exact_match:
                    break

            except Exception as e:
                self.logger.debug(f"[ubereats] Error revisando selector '{selector}': {e}")

        target = exact_match or partial_match

        if target:
            try:
                await target.scroll_into_view_if_needed()
                await target.click(timeout=5000)
                self.logger.info("[ubereats] Click realizado en el resultado seleccionado.")
                await self.random_delay(2.0, 3.0)
                return True
            except Exception as e:
                self.logger.warning(f"[ubereats] Error al hacer click en el resultado: {e}")
                try:
                    await target.evaluate("(el) => el.click()")
                    self.logger.info("[ubereats] Click JS realizado en el resultado seleccionado.")
                    await self.random_delay(2.0, 3.0)
                    return True
                except Exception as e2:
                    self.logger.warning(f"[ubereats] También falló JS click: {e2}")

        # ── 4. Fallback: presionar Enter y entrar al primer resultado ─────────
        self.logger.warning(f"[ubereats] No se encontró resultado coincidente para '{query}'. Intentando Enter.")
        try:
            await search_input.press("Enter")
            await self.random_delay(2.0, 3.0)
            # Intentar clic en el primer resultado de la página de resultados
            for selector in ["[data-testid='store-card']", "h3", "h2"]:
                try:
                    first = self.page.locator(selector).first
                    if await first.count() > 0 and await first.is_visible():
                        await first.click(timeout=5000)
                        self.logger.info(f"[ubereats] Primer resultado seleccionado tras Enter: {selector}")
                        await self.random_delay(2.0, 3.0)
                        return True
                except Exception:
                    continue
        except Exception as e:
            self.logger.warning(f"[ubereats] Error con Enter fallback: {e}")

        return False

    # --------------------------------------------------------------------------
    # Agregar producto buscando en el menú del restaurante
    

    async def _add_product_by_h4(self, product_name: str, retail: str = "") -> tuple:

        if not self.page:
            return False, None, None

        await self.random_delay(3.0, 4.0)

        normalized_target = self._normalize_text(product_name)
        self.logger.info(f"[ubereats] Buscando producto '{product_name}' en el menú...")

        # UberEats usa h3 para nombres de ítems del menú (a diferencia de Rappi con h4)
        product_selectors = ["h3", "h4", "[data-testid='rich-text']"]
        target_element = None

        for selector in product_selectors:
            elements = await self.page.locator(selector).all()
            if not elements:
                continue
            for el in elements:
                try:
                    if not await el.is_visible():
                        continue
                    text = await el.inner_text()
                    normalized_text = self._normalize_text(text)
                    if normalized_target == normalized_text or normalized_target in normalized_text:
                        target_element = el
                        self.logger.info(f"[ubereats] Producto encontrado con selector '{selector}': '{text}'")
                        break
                except Exception:
                    continue
            if target_element:
                break

        if not target_element:
            self.logger.warning(f"[ubereats] No se encontró el producto '{product_name}' en el menú")
            return False, None, None

        
    
        clickable = target_element.locator(
            "xpath=ancestor::*[@role='button' or @role='link' or contains(@data-testid, 'item') or contains(@data-testid, 'product')]"
        ).first
        if await clickable.count() == 0:
            clickable = target_element.locator("..")
        if await clickable.count() == 0:
            clickable = target_element

        try:
            await clickable.scroll_into_view_if_needed()
            await clickable.click(timeout=5000)
            await self.random_delay(1.5, 2.5)
            self.logger.info("[ubereats] Producto seleccionado, modal abierto")
        except Exception as e:
            self.logger.warning(f"[ubereats] Falló clic en contenedor del producto: {e}")
            try:
                await target_element.evaluate("el => el.click()")
                await self.random_delay(1.5, 2.5)
            except Exception:
                return False, None, None

        # Manejar personalizaciones
        try:
            await self._handle_product_customizations(retail)
        except Exception as e:
            self.logger.debug(f"[ubereats] Personalización de producto: {e}")

        # Extraer costo_total ANTES de hacer click en Agregar
        costo_total = await self._extract_total_from_add_to_cart()
        self.logger.info(f"[ubereats] costo_total extraído antes de agregar: {costo_total}")

        # Extraer costo_retail como precio adicional de la bebida seleccionada
        costo_retail = await self._extract_retail_addon_price(retail)
        self.logger.info(f"[ubereats] costo_retail extraído: {costo_retail}")

        # Botones de agregar al carrito en UberEats
        add_button_selectors = [
            "button[data-testid='add-to-cart-button']",
            "button[data-testid='quantity-button']",
            "button:has-text('Agregar al pedido')",
            "button:has-text('Agregar')",
            "button:has-text('Añadir')",
            "button:has-text('Añadir al carrito')",
            "button[data-testid*='add']",
            "button[type='submit']",
        ]

        added = False
        for selector in add_button_selectors:
            try:
                add_btn = self.page.locator(selector).first
                if await add_btn.count() > 0 and await add_btn.is_visible():
                    await add_btn.click(timeout=5000)
                    self.logger.info(f"[ubereats] Click en 'Agregar' usando selector: {selector}")
                    added = True
                    break
            except Exception:
                continue

        if not added:
            self.logger.warning(
                f"[ubereats] No se encontró botón de agregar para '{product_name}'. "
                "Retornando (False, None, None)."
            )
            return False, None, None

        # Cerrar modal si quedó abierto
        close_selectors = [
            "button[aria-label='Cerrar']",
            "button[aria-label='Close']",
            "button[data-testid='modal-close-button']",
            "button[class*='close']",
        ]
        for selector in close_selectors:
            try:
                close_btn = self.page.locator(selector).first
                if await close_btn.count() > 0 and await close_btn.is_visible():
                    await close_btn.click(timeout=2000)
                    self.logger.info("[ubereats] Modal cerrado")
                    break
            except Exception:
                continue

        await self.random_delay(1.0, 2.0)
        return True, costo_total, costo_retail

    # --------------------------------------------------------------------------
    # Manejo de personalizaciones


    async def _handle_product_customizations(self, retail: str = "") -> None:
        """
        Recorre los grupos de personalización del modal del producto en UberEats.
        Regla:
          - Para cada grupo de radio buttons: seleccionar la primera opción disponible.
          - EXCEPCIÓN: si el grupo corresponde a bebida, seleccionar la opción cuyo texto
            contenga el valor de `retail`.
          - Si no encuentra coincidencia en bebida, usar la primera opción.

        En UberEats, los grupos de personalización  estan en:
        - Secciones con encabezado h4/span + lista de opciones
        - data-testid='customization-section' o similar
        """
        if not self.page:
            return

        normalized_retail = self._normalize_text(retail) if retail else ""
        self.logger.info(f"[ubereats] Iniciando personalización con retail='{retail}'")

        # Detectar el modal del producto
        modal_root = None
        for selector in [
            "[data-testid='customization-modal']",
            "[data-testid='item-customization']",
            "[role='dialog'][aria-modal='true']",
            "[role='dialog']",
            "dialog",
            "[aria-modal='true']",
            "div[class*='Modal']",
            "div[class*='modal']",
        ]:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    modal_root = locator
                    self.logger.info(f"[ubereats] Modal detectado: {selector}")
                    break
            except Exception:
                continue
        if modal_root is None:
            modal_root = self.page.locator("body")
            self.logger.warning("[ubereats] No se detectó modal, usando body")

        # Detectar grupos de opciones
        group_lists = []
        for selector in [
            # UberEats agrupa personalizaciones en divs con role='group' o fieldset
            "fieldset",
            "[role='group']",
            "div:has(input[type='radio'])",
            "ul:has(input[type='radio'])",
        ]:
            try:
                locator = modal_root.locator(selector)
                count = await locator.count()
                if count > 0:
                    group_lists = [locator.nth(i) for i in range(count)]
                    self.logger.info(f"[ubereats] Detectados {count} grupos con '{selector}'")
                    break
            except Exception:
                continue

        if not group_lists:
            self.logger.info("[ubereats] No se encontraron grupos de personalización")
            return

        async def _select_radio(radio) -> bool:
            try:
                if await radio.is_visible() and await radio.is_enabled():
                    await radio.check(timeout=3000)
                    return True
            except Exception:
                pass
            try:
                radio_id = await radio.get_attribute("id")
                if radio_id:
                    label = modal_root.locator(f"label[for='{radio_id}']").first
                    if await label.count() > 0 and await label.is_visible():
                        await label.click(timeout=3000)
                        return True
            except Exception:
                pass
            try:
                label_ancestor = radio.locator("xpath=ancestor::label[1]").first
                if await label_ancestor.count() > 0 and await label_ancestor.is_visible():
                    await label_ancestor.click(timeout=3000)
                    return True
            except Exception:
                pass
            try:
                await radio.click(timeout=3000)
                return True
            except Exception:
                return False

        async def _get_radio_text(radio) -> str:
            try:
                radio_id = await radio.get_attribute("id")
                if radio_id:
                    label = modal_root.locator(f"label[for='{radio_id}']").first
                    if await label.count() > 0:
                        text = (await label.inner_text()).strip()
                        if text:
                            return text
            except Exception:
                pass
            try:
                label_ancestor = radio.locator("xpath=ancestor::label[1]").first
                if await label_ancestor.count() > 0:
                    text = (await label_ancestor.inner_text()).strip()
                    if text:
                        return text
            except Exception:
                pass
            try:
                parent = radio.locator("xpath=..")
                text = (await parent.inner_text()).strip()
                if text:
                    return text
            except Exception:
                pass
            return ""

        for idx, group in enumerate(group_lists):
            try:
                if not await group.is_visible():
                    continue

                group_title = ""
                try:
                    title_locator = group.locator(
                        "xpath=preceding::h3[1] | xpath=preceding::h4[1] | xpath=preceding::legend[1] | xpath=preceding::span[1]"
                    ).first
                    if await title_locator.count() > 0:
                        group_title = (await title_locator.inner_text()).strip()
                except Exception:
                    pass
                normalized_title = self._normalize_text(group_title)

                self.logger.info(f"[ubereats] Procesando grupo #{idx+1} - título='{group_title}'")

                radios = group.locator("input[type='radio']:not(:disabled)")
                radio_count = await radios.count()
                if radio_count == 0:
                    self.logger.debug(f"[ubereats] Grupo #{idx+1} sin radios")
                    continue

                is_beverage = any(
                    kw in normalized_title
                    for kw in ["bebida", "bebidas", "drink", "drinks", "refresco", "refrescos",
                               "gaseosa", "gaseosas", "elige tu bebida", "escoge tu bebida",
                               "acompaña tu combo", "beverage"]
                )

                selected = False

                if is_beverage and normalized_retail:
                    self.logger.info(f"[ubereats] Grupo de bebida - buscando coincidencia con retail='{retail}'")
                    for i in range(radio_count):
                        radio = radios.nth(i)
                        option_text = await _get_radio_text(radio)
                        normalized_option = self._normalize_text(option_text)
                        self.logger.debug(f"[ubereats] Opción {i+1}: '{option_text}'")
                        if normalized_retail in normalized_option:
                            if await _select_radio(radio):
                                self.logger.info(f"[ubereats] ✅ Seleccionada opción de bebida por retail: '{option_text}'")
                                selected = True
                                await self.random_delay(0.3, 0.6)
                                break
                    if not selected:
                        self.logger.warning(
                            f"[ubereats] No se encontró opción que contenga '{retail}' en grupo de bebida, "
                            "se usará primera opción"
                        )

                if not selected:
                    first_radio = radios.first
                    option_text = await _get_radio_text(first_radio)
                    if await _select_radio(first_radio):
                        self.logger.info(
                            f"[ubereats] ✅ Seleccionada primera opción del grupo '{group_title}': '{option_text}'"
                        )
                        await self.random_delay(0.3, 0.6)
                    else:
                        self.logger.warning(f"[ubereats] No se pudo seleccionar ninguna opción en grupo '{group_title}'")

            except Exception as e:
                self.logger.warning(f"[ubereats] Error procesando grupo #{idx+1}: {e}")

    # --------------------------------------------------------------------------
    # Extraer tiempo de entrega
    

    async def _extract_min_delivery_time(self) -> Optional[int]:
        """
        Extrae el tiempo mínimo de entrega desde la página del restaurante en UberEats.
        UberEats muestra el tiempo de entrega en el header del restaurante.
        Busca el patrón "N–M min" o "N min".
        """
        if not self.page:
            return None

        time_selectors = [
            "[data-testid='store-eta']",
            "[data-testid='delivery-time']",
            "[data-testid='eta']",
            # UberEats suele mostrar el ETA en un span cerca del nombre del restaurante
            "header [data-testid='rich-text']",
            "span:has-text('min')",
        ]

        for selector in time_selectors:
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
                for i in range(min(count, 5)):
                    el = locator.nth(i)
                    if not await el.is_visible():
                        continue
                    text = await el.inner_text()
                    match = re.search(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*min", text)
                    if match:
                        self.logger.info(f"[ubereats] Tiempo de entrega encontrado: '{text}'")
                        return int(match.group(1))
                    match = re.search(r"(\d{1,2})\s*min", text)
                    if match:
                        self.logger.info(f"[ubereats] Tiempo de entrega encontrado: '{text}'")
                        return int(match.group(1))
            except Exception:
                continue

        # Fallback: buscar en todo el texto de la página
        try:
            body_text = await self.page.locator("body").inner_text()
            match = re.search(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*min", body_text)
            if match:
                return int(match.group(1))
            match = re.search(r"(\d{1,2})\s*min", body_text)
            if match:
                return int(match.group(1))
        except Exception:
            pass

        self.logger.warning("[ubereats] No se encontró tiempo de entrega")
        return None

    # --------------------------------------------------------------------------
    #  Extraer costo de envío
    

    async def _extract_delivery_cost_from_p(self) -> Optional[str]:
        """
        Extrae el costo de envío desde la página del restaurante.
        UberEats muestra el costo de delivery en el header del restaurante
        o en el resumen del carrito.
        """
        if not self.page:
            return None

        delivery_selectors = [
            "[data-testid='delivery-fee']",
            "[data-testid='store-delivery-fee']",
            # Texto que contenga costo de envío
            "span:has-text('Envío')",
            "p:has-text('Envío')",
            "div:has-text('Costo de envío')",
        ]

        for selector in delivery_selectors:
            try:
                locator = self.page.locator(selector)
                count = await locator.count()
                for i in range(min(count, 5)):
                    el = locator.nth(i)
                    if not await el.is_visible():
                        continue
                    text = await el.inner_text()
                    match = re.search(r"\$\s*[\d,]+(?:\.\d{1,2})?", text)
                    if match:
                        price = match.group(0).replace(" ", "")
                        self.logger.info(f"[ubereats] Costo de envío encontrado: '{price}'")
                        return price
                    if "gratis" in text.lower() or "free" in text.lower():
                        self.logger.info("[ubereats] Envío gratis detectado")
                        return "$0"
            except Exception:
                continue

        # Fallback: JS para buscar costo de envío
        try:
            result = await self.page.evaluate(r"""() => {
                const priceRegex = /\$\s*[\d,]+(?:\.\d{1,2})?/;
                const keywords = ['envío', 'envio', 'delivery fee', 'costo de envío'];
                const all = Array.from(document.querySelectorAll('p, span, div'));
                for (const el of all) {
                    const text = (el.innerText || '').toLowerCase();
                    if (!keywords.some(k => text.includes(k))) continue;
                    const match = text.match(priceRegex);
                    if (match) return match[0].replace(/\s/g, '');
                    if (text.includes('gratis') || text.includes('free')) return '$0';
                }
                return null;
            }""")
            if result:
                self.logger.info(f"[ubereats] Costo de envío por JS: {result}")
                return result
        except Exception:
            pass

        self.logger.warning("[ubereats] No se encontró costo de envío")
        return None

    # --------------------------------------------------------------------------
    #  Extraer costo total del botón "Agregar al carrito"
 

    async def _extract_total_from_add_to_cart(self) -> Optional[str]:
        """
        Extrae el costo total desde el botón de agregar al carrito o desde el resumen.
        En UberEats, el botón de agregar suele mostrar el precio: "Agregar • $XXX"
        o hay un elemento con el subtotal visible en el modal.
        """
        if not self.page:
            return None

        # Estrategia 1: precio en el botón de agregar
        add_button_selectors = [
            "button[data-testid='add-to-cart-button']",
            "button:has-text('Agregar al pedido')",
            "button:has-text('Agregar')",
            "button[type='submit']",
        ]

        for selector in add_button_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    text = await locator.inner_text()
                    match = re.search(r"\$\s*[\d,]+(?:\.\d{1,2})?", text)
                    if match:
                        price = match.group(0).replace(" ", "")
                        self.logger.info(f"[ubereats] costo_total extraído del botón: {price}")
                        return price
            except Exception:
                continue

        # Estrategia 2: subtotal visible en el modal
        subtotal_selectors = [
            "[data-testid='subtotal']",
            "[data-testid='total']",
            "[data-testid='order-total']",
            "span:has-text('Total')",
            "div:has-text('Subtotal')",
        ]

        for selector in subtotal_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0 and await locator.is_visible():
                    text = await locator.inner_text()
                    match = re.search(r"\$\s*[\d,]+(?:\.\d{1,2})?", text)
                    if match:
                        price = match.group(0).replace(" ", "")
                        self.logger.info(f"[ubereats] costo_total extraído de subtotal: {price}")
                        return price
            except Exception:
                continue

        # Estrategia 3: JS genérico buscando el total
        try:
            result = await self.page.evaluate(r"""() => {
                const priceRegex = /\$\s*[\d,]+(?:\.\d{1,2})?/;

                // Buscar en botones de agregar
                const buttons = Array.from(document.querySelectorAll('button[type="submit"], button'));
                for (const btn of buttons) {
                    const text = (btn.innerText || '').toLowerCase();
                    if (text.includes('agregar') || text.includes('añadir') || text.includes('add')) {
                        const match = (btn.innerText || '').match(priceRegex);
                        if (match) return match[0].replace(/\s/g, '');
                    }
                }

                // Buscar por data-testid relacionados con precio
                const priceEls = Array.from(
                    document.querySelectorAll('[data-testid*="price"], [data-testid*="total"], [data-testid*="subtotal"]')
                );
                for (const el of priceEls) {
                    const match = (el.innerText || '').match(priceRegex);
                    if (match) return match[0].replace(/\s/g, '');
                }

                return null;
            }""")
            if result:
                self.logger.info(f"[ubereats] costo_total extraído por JS: {result}")
                return result
        except Exception as e:
            self.logger.warning(f"[ubereats] Error extrayendo costo_total: {e}")

        self.logger.warning("[ubereats] No se encontró costo_total")
        return None

    # --------------------------------------------------------------------------
    # precio adicional del retail seleccionado
    # --------------------------------------------------------------------------

    async def _extract_retail_addon_price(self, retail: str) -> Optional[str]:
        """
        Extrae el precio adicional de la opción de bebida/retail seleccionada.
        En UberEats, las opciones de personalización muestran el costo adicional
        como "+$XX" junto al nombre de la opción.
        """
        if not retail or not self.page:
            return None

        normalized_retail = self._normalize_text(retail)

        try:
            result = await self.page.evaluate(r"""(normalizedRetail) => {
                const addonRegex = /\+\s*\$\s*[\d,]+(?:\.\d{1,2})?/;
                const priceRegex = /\$\s*[\d,]+(?:\.\d{1,2})?/;

                // Buscar el radio chequeado o la opción seleccionada que coincida con retail
                const checkedInputs = Array.from(
                    document.querySelectorAll("input[type='radio']:checked, input[type='checkbox']:checked")
                );

                for (const input of checkedInputs) {
                    let container = input.id
                        ? document.querySelector(`label[for='${input.id}']`)
                        : null;
                    if (!container) container = input.closest('label') || input.parentElement;
                    if (!container) continue;

                    const containerText = (container.innerText || '').toLowerCase();
                    if (!containerText.includes(normalizedRetail)) continue;

                    // Buscar precio adicional "+$XX"
                    const addonMatch = (container.innerText || '').match(addonRegex);
                    if (addonMatch) return addonMatch[0].replace(/\s/g, '');
                }

                // Fallback: buscar por texto en labels/opciones
                if (normalizedRetail) {
                    const allOptions = Array.from(document.querySelectorAll('label, li, [role="option"], [role="radio"]'));
                    for (const el of allOptions) {
                        const text = (el.innerText || '').toLowerCase();
                        if (!text.includes(normalizedRetail)) continue;
                        const addonMatch = (el.innerText || '').match(addonRegex);
                        if (addonMatch) return addonMatch[0].replace(/\s/g, '');
                        const priceMatch = (el.innerText || '').match(priceRegex);
                        if (priceMatch) return priceMatch[0].replace(/\s/g, '');
                    }
                }

                return null;
            }""", normalized_retail)

            if result:
                self.logger.info(f"[ubereats] Precio addon retail '{retail}': {result}")
                return result
        except Exception as e:
            self.logger.warning(f"[ubereats] Error extrayendo retail addon: {e}")

        self.logger.warning(f"[ubereats] No se encontró precio addon para retail '{retail}'")
        return None

    # --------------------------------------------------------------------------
    #Obtener conteo del carrito


    async def _get_cart_count(self) -> int:

        if not self.page:
            return 0

        cart_count_selectors = [
            "[data-testid='cart-count']",
            "[data-testid='cart-button-count']",
            "[aria-label*='carrito']",
            "[aria-label*='cart']",
        ]

        for selector in cart_count_selectors:
            try:
                locator = self.page.locator(selector).first
                if await locator.count() > 0:
                    text = await locator.inner_text()
                    match = re.search(r"\d+", text)
                    if match:
                        return int(match.group(0))
            except Exception:
                continue

        return 0

    # --------------------------------------------------------------------------
    # MÉTODO: Guardar raw JSON (idéntico al de Rappi, empresa = UBEREATS)
    # --------------------------------------------------------------------------

    def _save_raw_json(
        self,
        address_id: str,
        order: str,
        tiempo_envio: Optional[int],
        costo_envio: Optional[str],
        costo_total: Optional[str],
        costo_retail: Optional[str],
        output_dir: str = ".",
    ) -> str:

        today = datetime.now().strftime("%Y%m%d")
        filename = f"raw_{today}.json"
        filepath = os.path.join(output_dir, filename)

        record = {
            "timestamp": datetime.now().isoformat(),
            "empresa": "UBEREATS",
            "address_id": address_id,
            "order": order,
            "tiempo_envio": tiempo_envio,
            "costo_envio": costo_envio,
            "costo_total": costo_total,
            "costo_retail": costo_retail,
        }

        existing = []
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
            except Exception as e:
                self.logger.warning(f"[ubereats] No se pudo leer {filepath}: {e}. Se sobreescribirá.")
                existing = []

        existing.append(record)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            self.logger.info(f"[ubereats] Datos guardados en {filepath}")
        except Exception as e:
            self.logger.error(f"[ubereats] Error guardando {filepath}: {e}")

        return filepath

    # --------------------------------------------------------------------------
    # MÉTODO PRINCIPAL: busca restaurante, agrega producto y extrae precios
    # --------------------------------------------------------------------------

    async def _scrape_restaurant_search_result(
        self,
        address_data: Dict[str, Any],
        order: str,
        retail: str = "",
    ) -> Dict[str, Any]:

        if not self.page:
            raise RuntimeError("Page no inicializada")

        address_id = address_data["id"]
        city = address_data["city"]
        zone_name = address_data["zone_name"]
        zone_type = address_data["zone_type"]

        self.logger.info(f"[ubereats] Buscando order: '{order}'")

        opened = await self._try_search_restaurant(order)
        screenshot_path = await self.save_screenshot(
            f"{address_id}_{self._slugify(order)}_search"
        )

        product_added = False
        costo_total = None
        costo_retail = None

        if opened:
            # Extraer tiempo_envio y costo_envio ANTES de abrir el modal del producto
            tiempo_envio = await self._extract_min_delivery_time()
            costo_envio = await self._extract_delivery_cost_from_p()
            self.logger.info(f"[ubereats] tiempo_envio: {tiempo_envio} | costo_envio: {costo_envio}")

            # Agregar producto y extraer precios
            result = await self._add_product_by_h4(order, retail)
            product_added, costo_total, costo_retail = result
            self.logger.info(f"[ubereats] Producto '{order}' agregado al carrito: {product_added}")
            self.logger.info(f"[ubereats] costo_total: {costo_total} | costo_retail: {costo_retail}")

            await self.save_screenshot(f"{address_id}_{self._slugify(order)}_cart")
        else:
            self.logger.warning("[ubereats] No se pudo abrir el restaurante, no se agrega producto")
            tiempo_envio = None
            costo_envio = None

        # ── Guardar en raw_YYYYMMDD.json ──────────────────────────────────────
        raw_json_path = self._save_raw_json(
            address_id=address_id,
            order=order,
            tiempo_envio=tiempo_envio,
            costo_envio=costo_envio,
            costo_total=costo_total,
            costo_retail=costo_retail,
        )
        self.logger.info(f"[ubereats] JSON raw guardado en: {raw_json_path}")

        visible_text = await self._extract_page_text()
        available = opened or self._is_restaurant_visible(visible_text, order)
        eta_min, eta_max = self._extract_eta(visible_text)
        discount_text = self._extract_discount(visible_text)
        cart_count = await self._get_cart_count() if opened else 0

        return self.build_result(
            address_id=address_id,
            city=city,
            zone_name=zone_name,
            zone_type=zone_type,
            restaurant=order,
            product_name=order,
            product_price=None,
            delivery_fee=None,
            service_fee=None,
            eta_min=eta_min,
            eta_max=eta_max,
            discount_text=discount_text,
            total_price=None,
            available=available,
            screenshot_path=screenshot_path,
            raw_metadata={
                "order_searched": order,
                "retail_used": retail,
                "restaurant_opened": opened,
                "product_added_to_cart": product_added,
                "cart_items_count": cart_count,
                "tiempo_envio_min": tiempo_envio,
                "costo_envio": costo_envio,
                "costo_total": costo_total,
                "costo_retail": costo_retail,
                "raw_json_path": raw_json_path,
                "search_text_sample": visible_text[:1000],
            },
        )

    # --------------------------------------------------------------------------
    # MÉTODOS AUXILIARES 
  

    async def _extract_page_text(self) -> str:
        if not self.page:
            return ""
        try:
            body = await self.page.locator("body").inner_text()
            return body.strip()
        except Exception:
            return ""

    def _is_restaurant_visible(self, page_text: str, restaurant_name: str) -> bool:
        return self._normalize_text(restaurant_name) in self._normalize_text(page_text)

    def _extract_eta(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        patterns = [
            r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*min",
            r"(\d{1,2})\s*a\s*(\d{1,2})\s*min",
            r"(\d{1,2})\s*min",
        ]
        for idx, pattern in enumerate(patterns):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if idx < 2:
                    return int(match.group(1)), int(match.group(2))
                else:
                    value = int(match.group(1))
                    return value, value
        return None, None

    def _extract_discount(self, text: str) -> Optional[str]:
        promo_patterns = [
            r"\b\d{1,3}%\s*OFF\b",
            r"\b\d+x1\b",
            r"\bEnvío gratis\b",
            r"\bEnvio gratis\b",
            r"\bDescuento\b.{0,30}",
            r"\bPromo\b.{0,30}",
        ]
        for pattern in promo_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return None

    def _normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        return text

    def _slugify(self, text: str) -> str:
        slug = text.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug