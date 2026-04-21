#modif LM 17/03

import streamlit as st
import pandas as pd

def extraction_donnees(fichier_excel):
    # Ce mapping fait le pont entre "N'importe quel nom dans l'Excel" 
    # et "Le nom unique utilisé dans le code"
    mapping_entree = {
        "matrice_distance": ["matrice Dist", "matrice_distance", "Distances"],
        "matrice_duree": ["matrice Durée", "matrice_duree", "Temps"],
        "m_flux": ["M flux", "m_flux", "Flux"],
        "param_contenants": ["param Contenants", "param_contenants", "Contenants"],
        "param_vehicules": ["param Véhicules", "param_vehicules", "Véhicules"],
        "param_sites": ["param Sites", "param_sites", "Sites"]
    }
    
    data_dict = {}
    try:
        with pd.ExcelFile(fichier_excel, engine='openpyxl') as xl:
            feuilles_dispo = xl.sheet_names
            
            for var_name, noms_possibles in mapping_entree.items():
                # On cherche quel nom de l'Excel correspond à notre variable
                nom_reel = next((n for n in noms_possibles if n in feuilles_dispo), None)
                
                if not nom_reel:
                    st.error(f"⚠️ Onglet introuvable. On cherchait l'un de ceux-là : {noms_possibles}")
                    return None
                
                df = pd.read_excel(xl, sheet_name=nom_reel)
                
                # --- STANDARDISATION DES DONNÉES ---
                
                if var_name == "param_sites":
                    # On garde Site, Adresse et Accessibilité pour nourrir 'accessibilite_sites' et 'adresses'
                    # On crée les deux clés demandées par les autres modules
                    data_dict["accessibilite_sites"] = df.iloc[:, [0, 2]].copy()
                    data_dict["accessibilite_sites"].columns = ["site", "accessibilite"]
                    
                    data_dict["adresses"] = df.iloc[:, [0, 1]].copy()
                    data_dict["adresses"].columns = ["site", "adresse"]
                    
                    # On garde aussi l'onglet complet sous le nom standard
                    data_dict["param_sites"] = df 
                else:
                    data_dict[var_name] = df
                    
        return data_dict
    except Exception as e:
        st.error(f"Erreur lors de l'extraction : {e}")
        return None
    
    data_dict = {}
    try:
        with pd.ExcelFile(fichier_excel, engine='openpyxl') as xl:
            for var_name, sheet_name in mapping.items():
                if sheet_name not in xl.sheet_names:
                    st.error(f"⚠️ Onglet manquant : {sheet_name}")
                    return None
                
                df = pd.read_excel(xl, sheet_name=sheet_name)
                
                # --- TRAITEMENT SPÉCIFIQUE DE L'ONGLET "param Sites" ---
                
                # Cas 1 : On ne garde que Site et Accessibilité (Colonnes A et C)
                if var_name == "accessibilite_sites":
                    df = df.iloc[:, [0, 2]]
                    df.columns = ["site", "accessibilite"]
                
                # Cas 2 : On ne garde que Site et Adresse (Colonnes A et B)
                elif var_name == "adresses":
                    df = df.iloc[:, [0, 1]]
                    df.columns = ["site", "adresse"]
                
                # -------------------------------------------------------
                
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
