#modif LM 17/03

import streamlit as st
import pandas as pd

def extraction_donnees(fichier_excel):
    mapping = {
        "matrice_distance": "matrice Dist",
        "matrice_duree": "matrice Durée",
        "m_flux": "M flux",
        "param_contenants": "param Contenants",
        "param_vehicules": "param Véhicules",
        "accessibilite_sites": "param Sites"
    }
    
    data_dict = {}
    try:
        with pd.ExcelFile(fichier_excel, engine='openpyxl') as xl:
            for var_name, sheet_name in mapping.items():
                if sheet_name not in xl.sheet_names:
                    st.error(f"⚠️ Onglet manquant : {sheet_name}")
                    return None
                
                df = pd.read_excel(xl, sheet_name=sheet_name)
                
                if var_name == "accessibilite_sites":
                    df = df.iloc[:, [0, 2]]
                    df.columns = ["site", "accessibilite"]
                
                data_dict[var_name] = df
        return data_dict
    except Exception as e:
        st.error(f"Erreur : {e}")
        return None

def show_import():
    st.header("⚙️ Importation des données")
    uploaded_file = st.file_uploader("Charger le fichier Excel", type=["xlsx"])
    
    if uploaded_file:
        if st.button("Lancer l'extraction", use_container_width=True):
            resultat = extraction_donnees(uploaded_file)
            if resultat:
                st.session_state["data"] = resultat
                st.success("✅ Données chargées !")

    # --- LA CORRECTION EST ICI ---
    if "data" in st.session_state:
        # On récupère l'objet du session_state pour l'utiliser localement
        data = st.session_state["data"] 
        
        st.divider()
        st.subheader("🔍 Vérification des variables")
        
        # Utilisation de colonnes ou expanders pour tout voir
        tab1, tab2, tab3 = st.tabs(["Matrices", "Flux & Sites", "Paramètres"])
        
        with tab1:
            st.write("**Matrice Distance**")
            st.dataframe(data["matrice_distance"], use_container_width=True)
            st.write("**Matrice Durée**")
            st.dataframe(data["matrice_duree"], use_container_width=True)
            
        with tab2:
            st.write("**Flux (m_flux)**")
            st.dataframe(data["m_flux"], use_container_width=True)
            st.write("**Accessibilité Sites**")
            st.dataframe(data["accessibilite_sites"], use_container_width=True)
            
        with tab3:
            st.write("**Contenants**")
            st.dataframe(data["param_contenants"], use_container_width=True)
            st.write("**Véhicules**")
            st.dataframe(data["param_vehicules"], use_container_width=True)
