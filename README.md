# Sistema-de-Competitive-Intelligence-para-Rappi-
Sistema de inteligencia competitiva basada en scraping y análisis de datos
Aplicación en Python para realizar scraping de plataformas de delivery (Rappi, Uber Eats y DiDi Food), almacenar los resultados en formato JSON y analizar comparativamente métricas clave mediante una interfaz gráfica
Scraping automatizado con Playwright
Soporte para múltiples plataformas:
  -Rappi
  -Uber Eats
  -DiDi Food
Estandarización de datos en JSON
Análisis interactivo con interfaz gráfica (Tkinter)
Comparación por:
  -Orden (producto)
  -Ubicación
  -Plataforma
Visualización mediante gráficas de barras
Tecnologías utilizadas
  -Python 3.9+
  -Playwright
  -Pandas
  -Matplotlib
  -Tkinter
Para instalar:
1. Clonar el repositorio
  git clone <repo-url>
  cd <repo-name>
2. Crear entorno virtual (recomendado)
  python -m venv venv
  source venv/bin/activate  # Linux / Mac
  venv\\Scripts\\activate     # Windows
3. Instalar dependencias
  pip install playwright pandas matplotlib
4. Instalar navegadores de Playwright
  playwright install
ESTRUCTURA:
rappi-competitive-intel/
├── README.md
├── requirements.txt
├── config/
│   └── addresses.json        # se almacenan las direcciones para analizar
├── scrapers/
│   ├── base_scraper.py       # clase base con retry, habilitación de screenshots y demás
│   ├── rappi_scraper.py
│   ├── UberEats_scraper.py
│   └── didi_scraper.py
├── main.py # punto de entrada: python runner.py
├── run_all_scrapers.py                   
├── data/
│   └──graficas.py
├── logs
├── screenshots/              # evidencia visual
|── results/
|    |── JSON
|    |── screenshots
----------------------------------------
Para configurar o añadir direcciones, se debe editar el archivo addresses.json, la estructura que maneja es la siguiente:
   {
      "id": "CDMX_POLANCO_01",
      "city": "Ciudad de Mexico",
      "zone_name": "Polanco",
      "zone_type": "premium_residential_commercial",
      "address": "Av. Presidente Masaryk 111, Polanco, Miguel Hidalgo, 11560 Ciudad de Mexico, CDMX, Mexico",
      "lat": 19.4326,
      "lng": -99.1900,
      "anchor_restaurants": ["McDonald's", "Burger King", "KFC"],
      "order": ["Home Office con Big Mac"],
      "retail": ["Coca-Cola"],
      "notes": "High-income zone with dense restaurant supply and strong platform competition."
    }
parámetros como order, retail y address son fundamentales parra la ejecución de los scrapers, los demás son elementos opcionales.
LOS SCRAPERS DE DIDIFOOD Y UBEREATS SOLICITAN ACCEDER CON USUARIO Y CONTRASEÑA, SE PROVEEN UNA SOLA VEZ EN LA EJECUCIÓN DE LOS SCRAPERS, PERO ES FUNDAMENTAL DÁRSELOS AL PROGRAMA CON USUARIOS EXISTENTES.
-------------------------------------------------------USO---------------------------------------------------------------
-LA EJECUCIÓN DEL PROGRAMA MUESTRA POR CONSOLA 3 MENSAJES:1. Iniciar scraping
                                                          2.Analizar Datos
                                                          3. Salir
La primera opción actualiza los JSON que se encuentran en la carpeta results, según la plataforma que corresponda hay un JSON para ella y según el día se genera un archivo nuevo a cada plataforma.
la segunda opción permite visualizar los datos almacenados en TODOS LOS JSON de la carpeta resultados, seleccionar la orden y aplicar filtro de localización para visualizar parámetros clave.
Es obligatorio seleccionar la orden para la graficación, el resto de parámetros son opcionales.Las capturas de pantalla de manera general se pueden visualizar en la carpeta de screenshots, las capturas por cada plataforma se pueden consultar en results/screenshots.LOS JSON en la parte externa del proyecto y que contienen en su nombre la palabra RAW, almacenan todos los datos de las ejecuciones, independientemente de la plataforma que se trate.
---------------------LIMITACIONES-------------------
Actualmente el software solo extrae algunos datos de la plataforma RAPPI, para el caso de didifood y uberEats, no extrae nada, solo escribe parámetros NULL en el JSON si se realiza el scraping, LOS DATOS ALMACENADOS ACTUALMENTE EN LOS JSON NO REPRESENTAN VALORES REALES DENTRO DE LAS PLATAFORMAS, FUERON LLENADOS DE MANERA ALEATORIA PARA DEMOSTRAR QUE EL APARTADO DE GRAFICACIÓN Y DIAGRAMAS DE BARRAS FUNCIONAN CORRECTAMENTE, es necesario mejorar y seguir trabajando en los scrapers para la extracción de los datos, no fue posible finalizarlo dada la complejidad de las páginas y el limitado tiempo con el que se contaba para desarrollar la herramienta. Las capturas de pantalla de manera general se pueden visualizar en la carpeta de screenshots, las capturas por cada plataforma se pueden consultar en results/screenshots. No es posible generar informes PDF.
---------------------------MEJORAS Y ACTUALIZACIONES-----------------------
De ser posible contar con más tiempo para el desarrollo, vale la pena complementar fuertemente los algoritmos para el scraping, así como integrar más opciones al análisis de datos,juntar más información y permitir al usuario modificar más parámetros para visualizarla, los tiempos de ejecución, también podrían mejorarse, así como implementar herramientas como proxies antidetección para recolectar mayor cantidad de datos.
El programa se mantiene dentro de los estándares éticos y de legalidad, no busca realizar ciberataques como saturación de solicitudes al servidor ni nada por el estilo, solo busca extraer datos al alcance de todo público de manera automatizada, para poder establecer las condiciones actuales de Rappi frente a la competencia.
    
   
