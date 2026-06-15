import streamlit as st
import pandas as pd

# On utilise le cache pour éviter de relire le fichier à chaque interaction
@st.cache_data(show_spinner="Extraction des données en cours...")
def extraction_donnees(fichier_excel):
    mapping = {
        "matrice_distance": "matrice Dist",
        "matrice_duree": "matrice Durée",
        "m_flux": "M flux",
        "param_contenants": "param Contenants",
        "param_vehicules": "param Véhicules",
        "param_sites": "param Sites",
        "adresses": "param Sites"
    }
    
    data_dict = {}
    try:
        # Utilisation de pd.ExcelFile pour ne pas rouvrir le fichier 7 fois
        with pd.ExcelFile(fichier_excel, engine='openpyxl') as xl:
            for var_name, sheet_name in mapping.items():
                if sheet_name not in xl.sheet_names:
                    st.error(f"⚠️ Onglet manquant : {sheet_name}")
                    return None
                
                df = pd.read_excel(xl, sheet_name=sheet_name)
                
                if var_name == "adresses":
                    df = df.iloc[:, [0, 1]]
                    df.columns = ["site", "adresse"]
                
                data_dict[var_name] = df
        return data_dict
    except Exception as e:
        st.error(f"Erreur lors de la lecture : {e}")
        return None

def show_import():
    st.header("⚙️ Importation des données")
    uploaded_file = st.file_uploader("Charger le fichier Excel", type=["xlsx"])
    
    if uploaded_file:
        # 1. On vérifie si on doit lancer l'extraction
        # Le bouton sert de déclencheur initial
        if st.button("Lancer l'extraction", use_container_width=True):
            resultat = extraction_donnees(uploaded_file)
            if resultat:
                st.session_state["data"] = resultat
                st.success("✅ Données chargées et mises en cache !")

    # 2. Affichage persistant
    if "data" in st.session_state:
        data = st.session_state["data"]
        
        st.divider()
        st.subheader("🔍 Vérification des variables")
        
        tab1, tab2, tab3 = st.tabs(["Matrices", "Flux & Sites", "Paramètres"])
        
        with tab1:
            col1, col2 = st.columns(2)
            with col1:
                st.write("**Matrice Distance**")
                st.dataframe(data["matrice_distance"], use_container_width=True)
            with col2:
                st.write("**Matrice Durée**")
                st.dataframe(data["matrice_duree"], use_container_width=True)
            
        with tab2:
            st.write("**Flux (m_flux)**")
            st.dataframe(data["m_flux"], use_container_width=True)
            st.write("**Accessibilité Sites**")
            st.dataframe(data["param_sites"], use_container_width=True)
            
        with tab3:
            st.write("**Contenants**")
            st.dataframe(data["param_contenants"], use_container_width=True)
            st.write("**Véhicules**")
            st.dataframe(data["param_vehicules"], use_container_width=True)
