import streamlit as st
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import math
import plotly.express as px
import plotly.graph_objects as go

@dataclass
class Job:
    id: str
    origine: str
    destination: str
    hub_origine: str
    type_contenant: str
    quantite: float
    poids_total: float
    surface_totale: float
    jour: str
    mutualise: bool
    nom_mutualisation: Optional[str]

@dataclass
class Tournee:
    id: str
    jour: str
    vehicule_type: str
    capacite_max: float
    hub_depart: str
    jobs: List[Job] = field(default_factory=list)
    itineraire: List[str] = field(default_factory=list)
    duree_totale: float = 0.0 
    distance_totale: float = 0.0
    remplissage_actuel: float = 0.0

@dataclass
class Chauffeur:
    id: str
    tournees: List[Tournee] = field(default_factory=list)
    temps_travail_cumule: float = 0.0

class MoteurSimulation:
    def __init__(self, data: Dict, params: Dict):
        self.data = data
        self.params = params
        self.hubs = self._construire_hubs()
        self.flotte = self._preparer_flotte()
        self.jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
      
        st.write('Init OK')  # Vérifiez les résultats dans la console
    
    def _construire_hubs(self) -> Dict[str, str]:
        """
        Identifie les sites situés à la même adresse géographique (distance = 0)
        pour permettre le groupage multi-flux sans dépendre des noms de sites.
        """
        matrice_dist = self.data["matrice_distance"].copy()
        
        # S'assurer que l'index de la matrice correspond aux noms des sites
        if matrice_dist.index.dtype in ['int64', 'int32']:
            col_sites = matrice_dist.columns[0]
            matrice_dist = matrice_dist.set_index(col_sites)
        
        sites = matrice_dist.index.tolist()
        mapping_site_hub = {}
        visites = set()

        for s1 in sites:
            if s1 not in visites:
                # On groupe tous les sites qui sont à 0 km de s1 (quais d'un même établissement)
                groupe = matrice_dist.index[matrice_dist[s1] == 0].tolist()
                
                # On crée un identifiant de HUB générique (ex: HUB_SITE_A)
                hub_id = f"HUB_{s1}" 
                
                for site in groupe:
                    mapping_site_hub[site] = hub_id
                    visites.add(site)
                    
        st.write('Hubs construit OK')  # Vérifiez les résultats dans la console            
        return mapping_site_hub

    def _preparer_flotte(self) -> pd.DataFrame:
        df_v = self.data["param_vehicules"].copy()
        col_type = "Types"
        flotte_sel = self.params.get("flotte_selectionnee", df_v[col_type].tolist())
        df_v['cap_tri'] = df_v.apply(lambda x: self._calculer_capacite_vehicule(x), axis=1)
        df_v = df_v.sort_values('cap_tri', ascending=False)
        
        st.write('Préparer flotte OK')  # Vérifiez les résultats dans la console
       
        return df_v[df_v[col_type].isin(flotte_sel)]

    def _calculer_capacite_vehicule(self, row_vehicule) -> float:
        tx_remplissage = self.params.get("taux_remplissage_max", 0.9)
        poids_raw = str(row_vehicule.get("Poids max chargement", "10")).split(' ')[0].replace(',', '.')
        try:
            return float(poids_raw) * tx_remplissage
        except:
            return 10.0 

    def _filtrer_et_normaliser_flux(self) -> pd.DataFrame:
        df_flux = self.data["m_flux"].copy()
        col_nature = "Nature du flux (les tournées sont elles à prévoir avec une obligation de transport ou une obligation de passage?)"
        if col_nature in df_flux.columns:
            return df_flux[df_flux[col_nature].astype(str).str.contains("Volume", na=False)]
        return df_flux

    def convertir_flux_en_jobs(self) -> List[Job]:
        d = st.session_state["data"]
        df_flux = d["m_flux"]
        # Référentiel contenants pour poids/dimensions
        ref_cont = d["param_contenants"].set_index("libellé").to_dict('index')
        
        jobs = []
        for idx, row in df_flux.iterrows():
            cont_type = str(row.get("Nature de contenant", ""))
            etat = str(row.get("Plein / vide", "")).upper()
            specs = ref_cont.get(cont_type, {})
            if not specs: continue

            # Sélection du poids selon l'état
            p_unit = specs.get("Poids plein (T)", 0) if "PLEIN" in etat else specs.get("Poids vide (T)", 0)
            s_unit = specs.get("dim longueur (m)", 0) * specs.get("dim largeur (m)", 0)

            for jour in self.jours:
                qte = row.get(f"Quantité {jour} ", 0) # Attention à l'espace final
                if pd.notnull(qte) and float(str(qte).replace(',','.')) > 0:
                    nb = float(str(qte).replace(',','.'))
                    jobs.append(Job(
                        id=f"J{idx}_{jour}",
                        origine=str(row.get("Point de départ", "")),
                        destination=str(row.get("Point de destination", "")),
                        hub_origine=self.hubs.get(str(row.get("Point de départ", "")), ""),
                        type_contenant=cont_type,
                        quantite=nb,
                        poids_total=nb * p_unit,
                        surface_totale=nb * s_unit,
                        jour=jour,
                        mutualise=str(row.get("Tournées mutualisées ? (OUI / NON)", "")).upper() == "OUI",
                        nom_mutualisation=str(row.get("Nom de la tournée mutualisée", ""))
                    ))
        return jobs

    def simuler(self):
        all_jobs = self.convertir_flux_en_jobs()
        tournees_finales = []
        for jour in self.jours:
            jobs_du_jour = [j for j in all_jobs if j.jour == jour]
            jobs_du_jour.sort(key=lambda x: x.quantite, reverse=True)
            tournees_jour = self._construire_tournees_jour(jour, jobs_du_jour)
            tournees_finales.extend(tournees_jour)
        chauffeurs = self.affecter_tournees_aux_chauffeurs(tournees_finales)
        #return self.generer_outputs(tournees_finales, chauffeurs)

         # Générer les résultats
        outputs = self.generer_outputs(tournees_finales, chauffeurs)

         # Ajoutez ceci pour vérifier ce qui est généré
        #print(outputs)  # Vérifiez les résultats dans la console 
        print('simul terminée')
    
         # Stocker les résultats dans session_state pour les utiliser dans Streamlit
        st.session_state['planning_detaille'] = outputs
    
    # Retourner les résultats pour un éventuel usage interne
        return outputs

    def _construire_tournees_jour(self, jour: str, jobs: List[Job]) -> List[Tournee]:
        # On récupère les données de session une seule fois pour la performance
        print(f"Début de la construction des tournées pour {jour}. Nombre de jobs: {len(jobs)}")
        d = st.session_state["data"]
        df_sites = d["param_sites"].set_index("Libellé")
        df_vehicules = d["param_vehicules"].set_index("Types")
        
        tournees = []
        jobs_restants = jobs.copy()
        
        while jobs_restants:
            # 1. Sélection du véhicule (ici le premier par défaut, à adapter si besoin)
            v_row = self.flotte.iloc[0] 
            v_type = v_row["Types"]
            v_specs = df_vehicules.loc[v_type]
            
            # Specs physiques du véhicule
            capa_poids = float(v_specs.get("Poids max chargement", 10))
            surf_max = float(v_specs.get("dim longueur interne (m)", 0)) * float(v_specs.get("dim largeur interne (m)", 0))

            job_initial = jobs_restants.pop(0)


            print(f"Destination: {job_initial.destination}")
            print(f"Type de véhicule (v_type): {v_type}")
            print("Index du DataFrame:", df_sites.index)
            print("Colonnes du DataFrame:", df_sites.columns)

            # Ensuite, vérifiez si la ligne et la colonne existent
            if job_initial.destination not in df_sites.index:
                print(f"Erreur: L'index '{job_initial.destination}' n'existe pas dans df_sites.")
            if v_type not in df_sites.columns:
                print(f"Erreur: La colonne '{v_type}' n'existe pas dans df_sites.")
            # --- CHECK ACCESSIBILITE SITE INITIAL ---
            # Utilisation de get() pour accéder de manière sûre à l'index et à la colonne
            value = df_sites.get(job_initial.destination, {}).get(v_type, None)

            # Vérification de la valeur
            if value is None or str(value).upper() != "OUI":
                # Si l'accès est possible mais que ce n'est pas "OUI", on logge également
                continue  # Si l'accessibilité échoue, on continue sans ajouter ce job à la tournée

            new_t = Tournee(
                id=f"T_{jour}_{len(tournees)+1}", 
                jour=jour,
                vehicule_type=v_type, 
                hub_depart=job_initial.hub_origine,
                capacite_max=capa_poids # On garde le poids comme capacité de référence pour l'affichage
            )
            
            # Initialisation des compteurs physiques
            new_t.poids_actuel = job_initial.poids_total
            new_t.surface_actuelle = job_initial.surface_totale
            new_t.jobs.append(job_initial)

            i = 0
            while i < len(jobs_restants):
                candidat = jobs_restants[i]
                
                # --- CONDITION 1 : MEME HUB ---
                if candidat.hub_origine != new_t.hub_depart:
                    i += 1
                    continue

                # --- CONDITION 2 : ACCESSIBILITE SITE (Quai / Type Véhicule) ---
                # On vérifie dans param_sites si ce Type de véhicule peut aller à la destination
                if str(df_sites.at[candidat.destination, v_type]).upper() != "OUI":
                    i += 1
                    continue

                # --- CONDITION 3 : COMPATIBILITÉ CONTENANT ---
                # On vérifie dans param_vehicules si le véhicule accepte ce contenant (colonne au nom du contenant)
                if str(v_specs.get(candidat.type_contenant, "NON")).upper() != "OUI":
                    i += 1
                    continue

                # --- CONDITION 4 : PHYSIQUE (POIDS & SURFACE) + MUTUALISATION ---
                impact_p = candidat.poids_total
                impact_s = candidat.surface_totale
                
                if candidat.mutualise:
                    deja_present = any(j.mutualise and j.nom_mutualisation == candidat.nom_mutualisation for j in new_t.jobs)
                    if deja_present:
                        impact_p = 0
                        impact_s = 0

                # On vérifie si ça passe en poids ET en surface au sol
                if (new_t.poids_actuel + impact_p <= capa_poids) and \
                   (new_t.surface_actuelle + impact_s <= surf_max):
                    
                    new_t.jobs.append(candidat)
                    new_t.poids_actuel += impact_p
                    new_t.surface_actuelle += impact_s
                    # Pour l'affichage global de ton indicateur 132% :
                    new_t.remplissage_actuel = new_t.poids_actuel 
                    
                    jobs_restants.pop(i)
                else:
                    i += 1
            
            # --- OPTIMISATION ET METRIQUES FINALES ---
            toutes_destinations = list(set([j.destination for j in new_t.jobs]))
            new_t.itineraire = self._optimiser_itineraire_tsp(new_t.hub_depart, toutes_destinations)
            
            # La fonction ci-dessous doit être mise à jour pour inclure les temps de manutention
            self._recalculer_metriques_tournee(new_t)
            
            tournees.append(new_t)
            
        return tournees

    def _recalculer_metriques_tournee(self, t: Tournee):
        # 1. Préparation des données de référence
        d = st.session_state["data"]
        df_sites = d["param_sites"].set_index("Libellé")
        
        # On récupère les caractéristiques du véhicule de la tournée
        # Assure-toi que t.vehicule_type correspond à un "Types" dans l'onglet véhicule
        v_specs = d["param_vehicules"].set_index("Types").loc[t.vehicule_type]
        
        duree, dist, curr = 0.0, 0.0, t.hub_depart
        
        # 2. Boucle sur l'itinéraire (Aller + Arrêts)
        for stop in t.itineraire:
            try:
                # --- TEMPS DE TRAJET ET DISTANCE ---
                dist += d["matrice_distance"].at[curr, stop]
                duree += d["matrice_duree"].at[curr, stop]
                
                # --- TEMPS FIXE : MISE À QUAI ---
                # On récupère la valeur "Temps de mise à quai - manœuvre, contact/admin (minutes)"
                tps_fixe = float(v_specs.get("Temps de mise à quai - manœuvre, contact/admin (minutes)", 0))
                duree += tps_fixe
                
                # --- TEMPS VARIABLE : MANUTENTION ---
                # A. Vérifier si le site a un quai (OUI/NON)
                presence_quai = str(df_sites.at[stop, "Présence de quai "]).upper().strip() == "OUI"
                
                # B. Sélectionner le bon ratio de manutention du véhicule
                if presence_quai:
                    ratio_manu = float(v_specs.get("Manutention avec quai (minutes / contenants)", 0))
                else:
                    ratio_manu = float(v_specs.get("Manutention sans quai (minutes / contenants)", 0))
                
                # C. Calculer le volume déposé/chargé sur ce site précis
                # On additionne la 'quantite' (nb de contenants) des jobs liés à ce stop
                nb_contenants_site = sum(j.quantite for j in t.jobs if j.destination == stop)
                
                # Ajout du temps de manutention (nb contenants * ratio)
                # Note : On multiplie par 2 si on considère chargement + déchargement
                duree += (nb_contenants_site * ratio_manu)
                
            except Exception as e:
                # Si un site manque dans la matrice ou les params, on garde une trace
                pass 
                
            curr = stop

        # 3. Retour au Hub (Trajet final)
        try:
            duree += d["matrice_duree"].at[curr, t.hub_depart]
            dist += d["matrice_distance"].at[curr, t.hub_depart]
        except: 
            pass
            
        # 4. Mise à jour de la tournée
        t.duree_totale = duree
        t.distance_totale = dist

    def _optimiser_itineraire_tsp(self, hub_depart: str, destinations: List[str]) -> List[str]:
        """
        Organise l'ordre des livraisons pour minimiser les kilomètres parcourus.
        Utilise l'algorithme du plus proche voisin.
        """
        if not destinations:
            return []
        
        itineraire_optimise = []
        # On dédoublonne les points de livraison pour ne pas s'arrêter deux fois au même quai
        points_a_visiter = list(set(destinations))
        position_actuelle = hub_depart

        while points_a_visiter:
            # On cherche le site le plus proche parmi ceux qui restent à livrer
            st.write(f"DEBUG - Position: '{position_actuelle}' | Liste à visiter: {points_a_visiter}")
            prochain_point = min(
                points_a_visiter, 
                key=lambda p: self.data["matrice_distance"].at[position_actuelle, p]
            )
            itineraire_optimise.append(prochain_point)
            points_a_visiter.remove(prochain_point)
            position_actuelle = prochain_point
            
        return itineraire_optimise
    
    def affecter_tournees_aux_chauffeurs(self, tournees: List[Tournee]) -> List[Chauffeur]:
        amp_max = self.params.get("contraintes_rh", {}).get("amplitude_max", 450)
        chauffeurs = []
        for jour in self.jours:
            t_jour = sorted([t for t in tournees if t.jour == jour], key=lambda x: x.duree_totale, reverse=True)
            ch_jour = []
            for t in t_jour:
                aff = False
                for c in ch_jour:
                    if c.temps_travail_cumule + t.duree_totale <= amp_max:
                        c.tournees.append(t); c.temps_travail_cumule += t.duree_totale
                        aff = True; break
                if not aff:
                    new_c = Chauffeur(id=f"CH_{jour}_{len(ch_jour)+1}")
                    new_c.tournees.append(t); new_c.temps_travail_cumule = t.duree_totale
                    ch_jour.append(new_c)
            chauffeurs.extend(ch_jour)
        return chauffeurs

    def generer_outputs(self, tournees: List[Tournee], chauffeurs: List[Chauffeur]) -> Dict:
        df_t = pd.DataFrame([{
            "ID Tournée": t.id, "Jour": t.jour, "Véhicule": t.vehicule_type,
            "Nb Jobs": len(t.jobs), "Distance (km)": round(t.distance_totale, 1),
            "Durée (min)": round(t.duree_totale, 1),
            "Remplissage (%)": round((t.remplissage_actuel / t.capacite_max)*100, 1) if t.capacite_max > 0 else 0
        } for t in tournees])
        
        nb_chauffeurs_par_jour = [len([c for c in chauffeurs if c.id.startswith(f"CH_{j}")]) for j in self.jours]
        
        res = {
            "tournees": df_t, "obj_chauffeurs": chauffeurs,
            "kpis": {
                "nb_tournees": len(tournees),
                "nb_chauffeurs_max_jour": max(nb_chauffeurs_par_jour) if nb_chauffeurs_par_jour else 0,
                "distance_totale": df_t["Distance (km)"].sum() if not df_t.empty else 0,
                "remplissage_moyen": df_t["Remplissage (%)"].mean() if not df_t.empty else 0,
                "temps_total_heures": (df_t["Durée (min)"].sum() / 60) if not df_t.empty else 0
            }
        }
        for j in self.jours:
            res[f"Quantité {j}"] = {"chauffeurs": [c.__dict__ for c in chauffeurs if c.id.startswith(f"CH_{j}")]}
        return res

def generer_visuel_bin_packing(tournee: Any):
    """
    Génère un graphique Plotly simulant la vue de dessus d'un camion.
    """
    L_camion = 6.0  # mètres
    l_camion = 2.4  # mètres
    
    fig = go.Figure()

    fig.add_shape(type="rect", x0=0, y0=0, x1=L_camion, y1=l_camion,
                  line=dict(color="Black", width=3))

    x_pos, y_pos = 0.1, 0.1
    largeur_cont = 0.8
    longueur_cont = 1.2
    
    destinations = list(set([j.destination for j in tournee.jobs]))
    colors = px.colors.qualitative.Safe
    color_map = {dest: colors[i % len(colors)] for i, dest in enumerate(destinations)}

    for job in tournee.jobs:
        for _ in range(int(job.quantite)):
            if x_pos + longueur_cont > L_camion:
                x_pos = 0.1
                y_pos += largeur_cont + 0.1
            
            if y_pos + largeur_cont > l_camion:
                break 

            fig.add_trace(go.Scatter(
                x=[x_pos, x_pos + longueur_cont, x_pos + longueur_cont, x_pos, x_pos],
                y=[y_pos, y_pos, y_pos + largeur_cont, y_pos + largeur_cont, y_pos],
                fill="toself",
                fillcolor=color_map[job.destination],
                name=f"{job.destination}",
                mode='lines',
                line=dict(color="white", width=1),
                text=f"Dest: {job.destination}<br>Type: {job.type_contenant}",
                hoverinfo="text"
            ))
            x_pos += longueur_cont + 0.1

    fig.update_layout(
        title=f"Plan de chargement - {tournee.vehicule_type}",
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        showlegend=False, margin=dict(l=10, r=10, t=40, b=10),
        height=300, plot_bgcolor="white"
    )
    return fig
