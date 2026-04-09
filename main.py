import subprocess
import sys
import os


def run_scraping():
    print("\n🚀 Ejecutando scraping...\n")

    try:
        subprocess.run(
            [sys.executable, "run_all_scrapers.py"],
            check=True
        )
    except subprocess.CalledProcessError:
        print("❌ Error ejecutando el scraping")


def run_analysis():
    print("\n📊 Abriendo módulo de análisis...\n")

    try:
        subprocess.run(
            [sys.executable, os.path.join("data", "graficas.py")],
            check=True
        )
    except subprocess.CalledProcessError:
        print("❌ Error ejecutando el análisis")


def menu():
    while True:
        print("\n==============================")
        print("   🍔 FOOD DELIVERY ANALYTICS")
        print("==============================")
        print("1. Iniciar scraping")
        print("2. Analizar datos")
        print("3. Salir")
        print("==============================")

        option = input("Selecciona una opción: ")

        if option == "1":
            run_scraping()

        elif option == "2":
            run_analysis()

        elif option == "3":
            print("\n👋 Saliendo del programa...")
            break

        else:
            print("⚠️ Opción inválida")


if __name__ == "__main__":
    menu()