import streamlit as st
import pandas as pd

def afficher_parametres_logistique():
    # 1. Sécurité : Vérifier si les données sont chargées
    if "data" not in st.session_state or "param_vehicules" not in st.session_state["data"]:
        st.warning("Veuillez d'abord charger un fichier Excel contenant l'onglet 'param Véhicules'.")
        return None

    df_vehicules = st.session_state["data"]["param_vehicules"].copy()
    
    st.header("🚐 Véhicules et Paramètres RH")

    # --- SECTION 1 : FLOTTE DE VÉHICULES (Dynamique) ---
    st.subheader("1. Sélection de la flotte pour la simulation")
    
    # On suppose que la colonne 0 est le nom du véhicule (PL, VL, etc.)
    col_nom = df_vehicules.columns[0]
    noms_vehicules = df_vehicules[col_nom].dropna().unique().tolist()
    
    col1, col2 = st.columns(2)
    flotte_active = []

    with col1:
        st.write("**Types disponibles dans l'Excel :**")
        for vehicule in noms_vehicules:
            # On crée une checkbox pour chaque véhicule trouvé dans l'Excel
            if st.checkbox(f"Inclure {vehicule}", value=True, key=f"check_{vehicule}"):
                flotte_active.append(vehicule)

    with col2:
        if not flotte_active:
            st.error("⚠️ Sélectionnez au moins un type de véhicule.")
        else:
            st.info(f"Flotte simulée : {', '.join(flotte_active)}")

    st.divider()

    # --- SECTION 2 : CONTRAINTES CHAUFFEURS (RH) ---
    st.subheader("2. Contraintes Chauffeurs & Postes")
    
    c1, c2, c3 = st.columns(3)
    with c1:
        duree_poste = st.number_input("Durée totale du poste (min)", value=450, step=15, help="Ex: 7h30 = 450 min")
        pause_obs = st.number_input("Pause obligatoire (min)", value=45, step=5)
    
    with c2:
        h_start = st.time_input("Prise de poste au plus tôt", value=pd.Timestamp("2024-01-01 06:00").time())
        h_end = st.time_input("Fin de poste au plus tard", value=pd.Timestamp("2024-01-01 21:00").time())

    with c3:
        t_prise = st.number_input("Préparation / Check véhicule (min)", value=20)
        t_fin = st.number_input("Nettoyage / Débrief (min)", value=15)

    st.divider()

    # --- SECTION 3 : SOUPLESSE & ALÉAS ---
    st.subheader("3. Optimisation et Sécurité")
    
    taux_remplissage = st.slider(
        "Taux de remplissage max cible (%)", 
        min_value=50, 
        max_value=100, 
        value=85,
        help="Réduire ce taux permet de garder de la place pour des imprévus ou des surplus de linge/repas."
    )

    # --- RETOUR DES PARAMÈTRES ---
    # Ces données seront utilisées par le moteur de calcul
    params_simu = {
        "vehicules_selectionnes": flotte_active,
        "rh": {
            "amplitude_totale": duree_poste,
            "pause": pause_obs,
            "h_prise_min": h_start,
            "h_fin_max": h_end,
            "temps_fixes": t_prise + t_fin
        },
        "securite_remplissage": taux_remplissage / 100
    }
    
    return params_simu
