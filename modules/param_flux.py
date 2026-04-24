import streamlit as st
import pandas as pd

def afficher_parametres_logistique():
    # 1. Vérification des données sources
    if "data" not in st.session_state or "param_vehicules" not in st.session_state["data"]:
        st.warning("⚠️ Veuillez charger un fichier Excel (onglet 'param Véhicules') pour configurer la flotte.")
        return

    # --- LOGIQUE DE PRÉ-REMPLISSAGE ---
    # On définit les valeurs qui seront injectées dans le 'value=' de chaque widget
    if "params_logistique" in st.session_state:
        p = st.session_state["params_logistique"]
        v_flotte = p.get("vehicules_selectionnes", [])
        v_duree = p.get("rh", {}).get("amplitude_totale", 450)
        v_pause = p.get("rh", {}).get("pause", 45)
        v_start = p.get("rh", {}).get("h_prise_min", pd.Timestamp("06:00").time())
        v_end = p.get("rh", {}).get("h_fin_max", pd.Timestamp("21:00").time())
        v_remplissage = int(p.get("securite_remplissage", 0.85) * 100)
        v_opt_rel = p.get("optimiser_reliquats_tournees", True)
        v_seuil_rel = p.get("seuil_rupture_reliquat", 80)
        config_existe = True
    else:
        v_flotte = [] # Par défaut on cochera tout si vide
        v_duree = 450
        v_pause = 45
        v_start = pd.Timestamp("06:00").time()
        v_end = pd.Timestamp("21:00").time()
        v_remplissage = 85
        v_opt_rel = True
        v_seuil_rel = 80
        config_existe = False

    df_vehicules = st.session_state["data"]["param_vehicules"].copy()
    col_nom = df_vehicules.columns[0]
    noms_vehicules = df_vehicules[col_nom].dropna().unique().tolist()

    st.header("🚐 Configuration de la Simulation Logistique")
    
    if config_existe:
        st.success("✅ Une configuration est actuellement enregistrée et chargée.")

    # --- FORMULAIRE TOUJOURS VISIBLE ---
    with st.form("form_parametres_log"):
        
        st.subheader("1. Sélection de la flotte")
        flotte_active = []
        cols_v = st.columns(3)
        for i, vehicule in enumerate(noms_vehicules):
            with cols_v[i % 3]:
                # Si aucune config n'existe, on coche tout par défaut
                # Sinon on vérifie si le véhicule est dans la liste enregistrée
                is_checked = vehicule in v_flotte if config_existe else True
                if st.checkbox(f"{vehicule}", value=is_checked):
                    flotte_active.append(vehicule)

        st.divider()

        st.subheader("2. Contraintes Chauffeurs (RH)")
        c1, c2, c3 = st.columns(3)
        with c1:
            duree_poste = st.number_input("Durée totale du poste (min)", value=v_duree, step=15)
            pause_obs = st.number_input("Pause obligatoire (min)", value=v_pause, step=5)
        with c2:
            h_start = st.time_input("Prise de poste min", value=v_start)
            h_end = st.time_input("Fin de poste max", value=v_end)
        with c3:
            t_prise = st.number_input("Préparation / Check (min)", value=20)
            t_fin = st.number_input("Nettoyage / Fin (min)", value=15)

        st.divider()

        st.subheader("3. Optimisation & Mutualisation")
        taux_remplissage = st.slider("Taux de remplissage max cible (%)", 50, 100, v_remplissage)

        opt_reliquats = st.checkbox(
            "🔄 Autoriser la réinjection des reliquats de tournées",
            value=v_opt_rel,
            help="Si coché, les camions des tournées imposées presque vides seront 'cassés' pour être optimisés."
        )
        
        seuil_reliquat = st.slider(
            "Seuil de remplissage pour réinjection (%)",
            min_value=10, max_value=90,
            value=v_seuil_rel,
            help="En dessous de ce taux, le camion est ré-injecté dans la pile d'optimisation."
        )

        submit_button = st.form_submit_button("💾 Enregistrer les modifications")

    # --- LOGIQUE DE SAUVEGARDE ---
    if submit_button:
        if not flotte_active:
            st.error("❌ Erreur : Vous devez sélectionner au moins un véhicule.")
        else:
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
                "optimiser_reliquats_tournees": opt_reliquats,
                "seuil_rupture_reliquat": seuil_reliquat,
                "statut": "CONFIGURÉ"
            }
            st.toast("Paramètres sauvegardés !") # Petit message discret en bas à droite
            st.rerun() # Recharge pour mettre à jour les indicateurs
