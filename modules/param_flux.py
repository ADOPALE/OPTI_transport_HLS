import streamlit as st
import pandas as pd

def afficher_parametres_logistique():
    # 1. Vérification des données sources
    if "data" not in st.session_state or "param_vehicules" not in st.session_state["data"]:
        st.warning("⚠️ Veuillez charger un fichier Excel (onglet 'param Véhicules') pour configurer la flotte.")
        return

    df_vehicules = st.session_state["data"]["param_vehicules"].copy()
    col_nom = df_vehicules.columns[0]
    noms_vehicules = df_vehicules[col_nom].dropna().unique().tolist()

    st.header("🚐 Configuration de la Simulation Logistique")

    # --- FORMULAIRE DE PARAMÉTRAGE ---
    # On utilise un formulaire pour regrouper les inputs et n'enregistrer qu'au clic
    with st.form("form_parametres_log"):
        
        st.subheader("1. Sélection de la flotte")
        flotte_active = []
        cols_v = st.columns(3)
        for i, vehicule in enumerate(noms_vehicules):
            # Répartit les cases à cocher sur 3 colonnes
            with cols_v[i % 3]:
                if st.checkbox(f"{vehicule}", value=True):
                    flotte_active.append(vehicule)

        st.divider()

        st.subheader("2. Contraintes Chauffeurs (RH)")
        c1, c2, c3 = st.columns(3)
        with c1:
            duree_poste = st.number_input("Durée totale du poste (min)", value=450, step=15)
            pause_obs = st.number_input("Pause obligatoire (min)", value=45, step=5)
        with c2:
            h_start = st.time_input("Prise de poste min", value=pd.Timestamp("06:00").time())
            h_end = st.time_input("Fin de poste max", value=pd.Timestamp("21:00").time())
        with c3:
            t_prise = st.number_input("Préparation / Check (min)", value=20)
            t_fin = st.number_input("Nettoyage / Fin (min)", value=15)

        st.divider()

        st.subheader("3. Optimisation")
        taux_remplissage = st.slider("Taux de remplissage max cible (%)", 50, 100, 85)


        st.subheader("⚙️ Stratégie de mutualisation")

        # Cette clé doit correspondre exactement à celle utilisée dans preparer_pile_optimisation
        st.session_state["params_logistique"]["optimiser_reliquats_tournees"] = st.checkbox(
            "🔄 Autoriser la réinjection des reliquats de tournées",
            value=True,
            help="Si coché, les camions des tournées imposées qui sont presque vides seront 'cassés' pour être regroupés avec d'autres flux solitaires allant dans la même direction."
        )
        
        # Optionnel : Ajout d'un curseur pour définir le seuil de "vide"
        st.session_state["params_logistique"]["seuil_rupture_reliquat"] = st.slider(
            "Seuil de remplissage pour réinjection (%)",
            min_value=10,
            max_value=90,
            value=80,
            help="En dessous de ce taux, le camion de la tournée imposée est considéré comme 'sous-optimisé' et sera envoyé au mélange."
        )

        # --- BOUTON D'ENREGISTREMENT ---
        submit_button = st.form_submit_button("💾 Enregistrer les paramètres")

    # --- LOGIQUE DE SAUVEGARDE ---
    if submit_button:
        if not flotte_active:
            st.error("❌ Erreur : Vous devez sélectionner au moins un véhicule.")
        else:
            # On stocke tout dans une clé dédiée du session_state
            st.session_state["params_logistique"] = {
                "vehicules_selectionnes": flotte_active,
                "rh": {
                    "amplitude_totale": duree_poste,
                    "pause": pause_obs,
                    "h_prise_min": h_start,
                    "h_fin_max": h_end,
                    "temps_fixes": t_prise + t_fin,
                    "temps_productif_max": duree_poste - (pause_obs + t_prise + t_fin)
                },
                "securite_remplissage": taux_remplissage / 100,
                "statut": "CONFIGURÉ"
            }
            st.success("✅ Paramètres enregistrés avec succès ! Vous pouvez passer à l'optimisation.")

    # Affichage d'un résumé si déjà configuré
    elif "params_logistique" in st.session_state:
        st.info(f"ℹ️ Configuration actuelle : {len(st.session_state['params_logistique']['vehicules_selectionnes'])} véhicules inclus.")
