#modif LM 17/03

import streamlit as st
import pandas as pd

def extraction_donnees(fichier_excel):
    # 1. Le mapping flexible
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
                nom_reel = next((n for n in noms_possibles if n in feuilles_dispo), None)
                
                if not nom_reel:
                    st.error(f"⚠️ Onglet introuvable : {noms_possibles}")
                    return None
              
                # Lecture de l'onglet trouvé
                df = pd.read_excel(xl, sheet_name=nom_reel)
                
                # --- 1. NETTOYAGE GÉNÉRAL (Indispensable) ---
                # Supprime les espaces et force les majuscules partout
                df.columns = [str(c).strip().upper() for c in df.columns]
                df = df.map(lambda x: x.strip().upper() if isinstance(x, str) else x)
                
                # --- 2. HARMONISATION "HUB_" POUR LES MATRICES ---
                if var_name in ["matrice_distance", "matrice_duree"]:
                    # On ajoute "HUB_" aux noms des sites (lignes et colonnes) 
                    # pour que ça matche avec l'ID généré par le moteur
                    
                    # Pour la 1ère colonne (les lignes)
                    df.iloc[:, 0] = df.iloc[:, 0].apply(lambda x: f"HUB_{x}" if not x.startswith("HUB_") else x)
                    
                    # Pour les en-têtes (les colonnes), sauf la 1ère qui est le titre
                    new_cols = [df.columns[0]]
                    for c in df.columns[1:]:
                        new_name = f"HUB_{c}" if not str(c).startswith("HUB_") else c
                        new_cols.append(new_name)
                    df.columns = new_cols
                    
                    # Enfin, on définit l'index
                    df = df.set_index(df.columns[0])
                
                # --- 3. TRAITEMENT DES SITES ---
                if var_name == "param_sites":
                    data_dict["accessibilite_sites"] = df.iloc[:, [0, 2]].copy()
                    data_dict["accessibilite_sites"].columns = ["site", "accessibilite"]
                    
                    data_dict["adresses"] = df.iloc[:, [0, 1]].copy()
                    data_dict["adresses"].columns = ["site", "adresse"]
                    
                    data_dict["param_sites"] = df 
                else:
                    data_dict[var_name] = df
                    
        return data_dict

    except Exception as e:
        st.error(f"Erreur lors de l'extraction : {e}")
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
