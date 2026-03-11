import streamlit as st
import pandas as pd

# --- CONFIGURATION ---
st.set_page_config(page_title="Logistique & Bio Optim", layout="wide")

# --- FONCTIONS DES DIFFÉRENTES VUES ---

def show_accueil():
    st.title("🏠 Accueil - Plateforme d'Optimisation")
    st.markdown("""
    Bienvenue dans votre outil de gestion logistique. Cette application vous permet de :
    * **⚙️ Paramétrage** : Importer vos référentiels (sites, capacités).
    * **📦 Volumes** : Analyser les flux de distribution.
    * **🔬 Biologie** : Suivre les passages et prélèvements.
    * **🏎️ Simulation** : Optimiser vos tournées via algorithmes.
    """)
    
    # Un petit tableau de bord rapide sur l'accueil
    col1, col2 = st.columns(2)
    with col1:
        st.info("**Étape 1 :** Commencez par importer vos données de paramétrage.")
    with col2:
        st.success("**Étape 2 :** Lancez la simulation pour voir les gains potentiels.")

def show_parametrage():
    st.title("⚙️ Importer des données de paramétrage")
    uploaded_file = st.file_uploader("Choisir un fichier CSV ou Excel", type=['csv', 'xlsx'])
    if uploaded_file:
        st.success("Fichier chargé avec succès !")

def show_volumes():
    st.title("📦 Volumes de distribution")
    st.bar_chart({"Volumes": [10, 25, 15, 30, 45]}) # Exemple

def show_biologie():
    st.title("🔬 Passages de biologie")
    st.write("Suivi des échantillons et des temps de passage.")

def show_optimisation():
    st.title("🏎️ Simulation et Optimisation des tournées")
    if st.button("Lancer l'algorithme d'optimisation"):
        with st.spinner("Calcul en cours..."):
            # Simulation d'un calcul lourd
            import time
            time.sleep(2)
            st.success("Tournées optimisées ! Gain estimé : 12% de distance.")

# --- BARRE LATÉRALE (NAVIGATION) ---

with st.sidebar:
    st.image("https://www.gstatic.com/images/branding/product/2x/ps_rhombus_64dp.png", width=50)
    st.title("Navigation")
    
    selection = st.radio(
        "Aller vers :",
        [
            "🏠 Accueil", 
            "⚙️ Paramétrage", 
            "📦 Volumes de distribution", 
            "🔬 Passages biologie", 
            "🏎️ Simulation & Optimisation"
        ]
    )
    st.divider()
    st.caption("Version Expert v1.2")

# --- LOGIQUE D'AFFICHAGE ---

if selection == "🏠 Accueil":
    show_accueil()
elif selection == "⚙️ Paramétrage":
    show_parametrage()
elif selection == "📦 Volumes de distribution":
    show_volumes()
elif selection == "🔬 Passages biologie":
    show_biologie()
elif selection == "🏎️ Simulation & Optimisation":
    show_optimisation()
