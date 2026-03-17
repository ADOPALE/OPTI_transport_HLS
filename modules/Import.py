#modif LM 17/03

import streamlit as st
import pandas as pd

def extraction_donnees(fichier_excel):
    """Logique d'extraction multi-onglets"""
    mapping = {
        "matrice_distance": "matrice Dist",
        "matrice_duree": "matrice Durée",
        "m_flux": "M flux",
        "param_contenants": "param Contenants",
        "param_vehicules": "param Véhicules",
        "accessibilite_sites": "param Sites"
    }
    
    data = {}
    try:
        # Utilisation de pd.ExcelFile pour ne pas rouvrir le fichier 6 fois
        with pd.ExcelFile(fichier_excel, engine='openpyxl') as xl:
            for var_name, sheet_name in mapping.items():
                if sheet_name not in xl.sheet_names:
                    st.error(f"⚠️ Onglet manquant : '{sheet_name}'")
                    return None
                
                df = pd.read_excel(xl, sheet_name=sheet_name)
                
                # Extraction spécifique pour l'accessibilité (Colonnes A et C)
                if var_name == "accessibilite_sites":
                    df = df.iloc[:, [0, 2]]
                    df.columns = ["site", "accessibilite"]
                
                data[var_name] = df
        return data
    except Exception as e:
        st.error(f"❌ Erreur lors de la lecture : {e}")
        return None

def show_import():
    st.header("⚙️ Importation des données")
    uploaded_file = st.file_uploader("Charger le fichier Excel de paramétrage", type=["xlsx"])
    
    if uploaded_file:
        if st.button("Lancer l'extraction des tables", use_container_width=True):
            resultat = extraction_donnees(uploaded_file)
            if resultat:
                # Stockage dans la variable unique demandée
                st.session_state["data"] = resultat
                st.success("✅ Toutes les tables ont été extraites et stockées !")

    # Vérification visuelle si les données sont en mémoire
    if "data" in st.session_state:
        d = st.session_state["data"]
        st.divider()
        
        # Affichage sélectif pour ne pas encombrer l'écran
        choix = st.selectbox("Visualiser une table :", list(d.keys()))
        st.dataframe(d[choix], use_container_width=True)
