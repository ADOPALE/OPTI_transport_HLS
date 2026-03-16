#__modif LM 16/03 --> ancien fichier téléchargé 

import streamlit as st
import pandas as pd

def extraction_donnees(fichier_excel):
    """
    Lit les 5 onglets spécifiques et renvoie un dictionnaire de DataFrames.
    """
    # Configuration de la correspondance Onglet Excel -> Clé Python
    mapping_onglets = {
        "param Véhicules": "param_vehicules",
        "param Sites": "param_sites",
        "param Contenants": "param_contenants",
        "param RH": "param_rh",
        "M flux": "m_flux"
    }
    
    data_extraite = {}
    
    try:
        # Ouverture du moteur Excel
        xl = pd.ExcelFile(fichier_excel, engine='openpyxl')
        onglets_presents = xl.sheet_names
        
        # Vérification et lecture de chaque onglet
        for nom_excel, cle_python in mapping_onglets.items():
            if nom_excel not in onglets_presents:
                st.error(f"⚠️ L'onglet obligatoire '**{nom_excel}**' est introuvable dans le fichier.")
                return None
            
            # Lecture de l'onglet
            data_extraite[cle_python] = pd.read_excel(xl, sheet_name=nom_excel)
            
        return data_extraite

    except Exception as e:
        st.error(f"❌ Erreur critique lors de la lecture du fichier : {e}")
        return None

def show_import():
    """
    Interface Streamlit pour l'importation.
    """
    st.header("⚙️ Importation des données")
    
    uploaded_file = st.file_uploader("Charger le fichier Excel de paramétrage (.xlsx)", type=["xlsx"])
    
    if uploaded_file:
        # Extraction via la fonction dédiée
        donnees = extraction_donnees(uploaded_file)
        
        if donnees:
            # Stockage dans le session_state pour accès global
            st.session_state["data"] = donnees
            
            st.success("✅ Fichier chargé et validé ! Les 5 onglets ont été importés.")
            
            # Affichage de l'état du chargement
            cols = st.columns(5)
            for i, (cle, df) in enumerate(donnees.items()):
                cols[i % 5].metric(cle, f"{len(df)} lignes")
    else:
        if "data" in st.session_state:
            st.info("💡 Des données sont déjà chargées en mémoire.")
