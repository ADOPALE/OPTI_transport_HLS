import streamlit as st 
import folium
from streamlit_folium import st_folium


def show_biologie_page():
    st.title("🧪 Paramétrage des Passages Biologie")

    if "data" not in st.session_state:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel.")
        return

    # 1. Récupération des données m_flux
    df_flux = st.session_state["data"]["m_flux"].copy()
    
    # On filtre les lignes où la colonne K (index 10) contient "fréquence"
    # Note : .iloc[:, 10] correspond à la colonne K
    df_freq = df_flux[df_flux.iloc[:, 10].astype(str).str.lower().str.contains("Fréquences", na=False)]

    if df_freq.empty:
        st.error("❌ Aucune ligne avec la mention 'Fréquences' n'a été trouvée dans la colonne K de l'onglet M flux.")
        return

    # 2. Vérification de l'unicité des sites (Colonne A, index 0)
    sites_counts = df_freq.iloc[:, 0].value_counts()
    doublons = sites_counts[sites_counts > 1].index.tolist()

    if doublons:
        st.error(f"❌ Erreur de structure : Les sites suivants apparaissent plusieurs fois avec la mention 'Fréquences' : {', '.join(doublons)}")
        st.info("💡 Veuillez corriger votre fichier Excel pour qu'un site n'ait qu'une seule ligne 'Fréquences' et réimportez-le.")
        return

    # 3. Paramètres globaux
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        duree_max = st.number_input("Durée max tournée (min)", value=120)
    with col_g2:
        temps_coll = st.number_input("Temps de collecte (min)", value=10)

    st.divider()
    st.subheader("🏥 Configuration automatique des sites (Flux Fréquence)")

    current_sites_config = {}

    # 4. Boucle sur les sites filtrés
    for _, row in df_freq.iterrows():
        site_name = row.iloc[0] # Colonne A
        if site_name == "HLS": continue 

        # Conversion des heures Excel (Colonnes S=18, T=19) en minutes
        # On gère le cas où la cellule serait vide
        try:
            h_ouvert = int(float(row.iloc[18]) * 1440) if pd.notnull(row.iloc[18]) else 480
            h_ferme = int(float(row.iloc[19]) * 1440) if pd.notnull(row.iloc[19]) else 1080
            nb_passages = int(row.iloc[22]) if pd.notnull(row.iloc[22]) else 3 # Colonne W=22
        except:
            h_ouvert, h_ferme, nb_passages = 480, 1080, 3

        cols = st.columns([1, 4])
        is_active = cols[0].checkbox("Inclure", value=True, key=f"check_{site_name}")
        
        if is_active:
            with cols[1].expander(f"📍 {site_name}", expanded=False):
                c1, c2 = st.columns([3, 1])
                with c1:
                    res = st.select_slider(
                        f"Plage horaire (Importée de l'Excel)",
                        options=range(0, 1441, 15),
                        value=(h_ouvert - (h_ouvert % 15), h_ferme - (h_ferme % 15)),
                        format_func=lambda x: f"{x//60:02d}:{x%60:02d}",
                        key=f"slide_{site_name}"
                    )
                with c2:
                    freq = st.number_input(f"Passages", min_value=1, value=nb_passages, key=f"freq_{site_name}")

                current_sites_config[site_name] = {'open': res[0], 'close': res[1], 'freq': freq}
        else:
            cols[1].info(f"❄️ {site_name} exclu.")

    st.subheader("🚐 Contraintes RH & Chauffeurs")
    c1, c2, c3 = st.columns(3)
    with c1:
        amplitude_poste = st.number_input("Durée du poste (min)", value=450, help="7h30 = 450 minutes")
    with c2:
        pause_dej = st.number_input("Pause déjeuner (min)", value=30)
    with c3:
        temps_releve = st.number_input("Temps de relève (min)", value=15, help="Temps entre deux chauffeurs sur le même véhicule")

    # Ajouter ces valeurs dans le dictionnaire de sauvegarde
    if st.button("💾 Enregistrer la configuration"):
        st.session_state["biologie_config"] = {
            "duree_max": duree_max,
            "temps_collecte": temps_coll,
            "sites": current_sites_config,
            "rh": {
                "amplitude": amplitude_poste,
                "pause": pause_dej,
                "releve": temps_releve
            }
        }
        st.success(f"Configuration enregistrée : {len(current_sites_config)} sites actifs.")
