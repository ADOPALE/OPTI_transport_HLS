#modif LM 17/03

import streamlit as st
import pandas as pd

def extraction_donnees(fichier_excel):
    """Extraction des onglets et stockage dans un dictionnaire unique"""
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
        with pd.ExcelFile(fichier_excel, engine='openpyxl') as xl:
            for var_name, sheet_name in mapping.items():
                if sheet_name not in xl.sheet_names:
                    st.error(f"⚠️ Onglet '{sheet_name}' introuvable.")
                    return None
                
                df = pd.read_excel(xl, sheet_name=sheet_name)
                
                # Nettoyage spécifique Accessibilité (Col A et C)
                if var_name == "accessibilite_sites":
                    df = df.iloc[:, [0, 2]]
                    df.columns = ["site", "accessibilite"]
                
                data[var_name] = df
        return data
    except Exception as e:
        st.error(f"❌ Erreur critique : {e}")
        return None

def show_import():
    st.header("⚙️ Importation des données")
    
    uploaded_file = st.file_uploader("Charger le fichier Excel de paramétrage", type=["xlsx"])
    
    if uploaded_file:
        if st.button("Lancer l'extraction et vérifier les données", use_container_width=True):
            resultat = extraction_donnees(uploaded_file)
            if resultat:
                st.session_state["data"] = resultat
                st.success("✅ Données extraites avec succès !")

    # --- SECTION VÉRIFICATION VISUELLE ---
    if "data" in st.session_state:
        data = st.session_state["data"]
        
        st.divider()
        st.subheader("🔍 Contenu des variables extraites")
        
        # Affichage structuré pour vérification rapide
        with st.expander("1. Matrice Distance", expanded=False):
            st.dataframe(data["matrice_distance"], use_container_width=True)
            
        with st.expander("2. Matrice Durée", expanded=False):
            st.dataframe(data["matrice_duree"], use_container_width=True)
            
        with st.expander("3. Tableau Flux (M flux)", expanded=False):
            st.dataframe(data["m_flux"], use_container_width=True)
            
        with st.expander("4. Paramètres Contenants", expanded=False):
            st.dataframe(data["param_contenants"], use_container_width=True)
            
        with st.expander("5. Paramètres Véhicules", expanded=False):
            st.dataframe(data["param_vehicules"], use_container_width=True)
            
        with st.expander("6. Accessibilité Sites", expanded=False):
            st.dataframe(data["accessibilite_sites"], use_container_width=True)
