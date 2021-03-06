<template>
  <div>
    <base-component title="Apps Menu" icon="mdi-menu-left" url="/" :loading="loading">
      <template #default>
        <v-card class="pa-3 ml-3">
          <v-card-title class="headline">
            <v-avatar size="50px" class="mr-5" tile>
              <v-img v-if="solution.image" :src="solution.image"></v-img>
              <v-icon v-else color="primary">{{solution.icon}} mdi-48px</v-icon>
            </v-avatar>
            <span>{{solution.name}}</span>
            <v-tooltip top>
              <template v-slot:activator="{ on, attrs }">
                <a
                  class="chatflowInfo"
                  :href="`https://now.threefold.io/#/${solution.type}`"
                  target="blank"
                >
                  <v-icon
                    color="primary"
                    large
                    v-bind="attrs"
                    v-on="on"
                    right
                  >mdi-information-outline</v-icon>
                </a>
              </template>
              <span>Go to How-to Manual</span>
            </v-tooltip>
          </v-card-title>

          <v-card-text>
            <span>{{solution.description}}</span>
            <br />
            <br />
            <v-btn color="primary" @click.stop="restart(solution.type)">New</v-btn>
            <v-btn
              color="primary"
              v-if="started(solution.type)"
              @click.stop="open(solution.type)"
            >Continue</v-btn>

            <v-divider class="my-5"></v-divider>

            <v-data-table
              :loading="loading"
              :headers="headers"
              :items="deployedSolutions"
              class="elevation-1"
            >
              <template slot="no-data">No {{solution.name.toLowerCase()}} instances available</p></template>
              <template v-slot:item.domain="{ item }">
                <a :href="`https://${item.Domain}/`">{{item.Domain}}</a>
              </template>
              <template v-slot:item.Expiration="{ item }">
                <div>{{ new Date(item.Expiration * 1000).toLocaleString() }}</div>
              </template>
              <template v-slot:item.actions="{ item }">
                <v-tooltip top>
                  <template v-slot:activator="{ on, attrs }">
                    <v-btn icon :href="`https://${item.Domain}/`" target="_blank">
                      <v-icon v-bind="attrs" v-on="on" color="primary">mdi-web</v-icon>
                    </v-btn>
                  </template>
                  <span>Open in browser</span>
                </v-tooltip>
                <v-tooltip top>
                  <template v-slot:activator="{ on, attrs }">
                    <v-btn icon @click.stop="deleteSolution(item)">
                      <v-icon v-bind="attrs" v-on="on" color="#810000">mdi-delete</v-icon>
                    </v-btn>
                  </template>
                  <span>Delete</span>
                </v-tooltip>
                <v-tooltip top>
                  <template v-slot:activator="{ on, attrs }">
                    <v-btn icon @click.stop="showInfo(item)">
                      <v-icon v-bind="attrs" v-on="on" color="#206a5d">mdi-information-outline</v-icon>
                    </v-btn>
                  </template>
                  <span>Show Information</span>
                </v-tooltip>
              </template>
            </v-data-table>
          </v-card-text>
        </v-card>
      </template>
    </base-component>
    <solution-info v-if="selected" v-model="dialogs.info" :data="selected"></solution-info>
    <cancel-solution v-if="selected" v-model="dialogs.cancelSolution" :wids="selected.wids"></cancel-solution>
  </div>
</template>

<script>
module.exports = {
  props: { type: String },
  components: {
    "solution-info": httpVueLoader("./Info.vue"),
    "cancel-solution": httpVueLoader("./Delete.vue")
  },
  data() {
    return {
      loading: true,
      selected: null,
      dialogs: {
        info: false,
        cancelSolution: false
      },
      headers: [
        { text: "Name", value: "Name" },
        { text: "URL", value: "domain" },
        { text: "Expiration", value: "expiration" },
        { text: "Actions", value: "actions", sortable: false }
      ],
      deployedSolutions: [],
      solutions: [...Object.values(APPS)]
    };
  },
  computed: {
    solution() {
      return this.solutions.find(obj => {
        return obj.type === this.type;
      });
    }
  },
  methods: {
    open(solutionId) {
      this.$router.push({
        name: "SolutionChatflow",
        params: { topic: solutionId }
      });
    },
    restart(solutionId) {
      localStorage.removeItem(solutionId);
      this.open(solutionId);
    },
    started(solution_type) {
      return localStorage.hasOwnProperty(solution_type);
    },
    showInfo(data) {
      this.selected = data;
      this.dialogs.info = true;
    },
    deleteSolution(data) {
      this.selected = data;
      this.dialogs.cancelSolution = true;
    },
    getDeployedSolutions(solution_type) {
      this.$api.solutions
        .getDeployed(solution_type)
        .then(response => {
          this.deployedSolutions = response.data.data;
        })
        .finally(() => {
          this.loading = false;
        });
    }
  },
  mounted() {
    this.getDeployedSolutions(this.type);
  }
};
</script>

<style scoped>
a.chatflowInfo {
  text-decoration: none;
  position: absolute;
  right: 10px;
  top: 10px;
}
</style>
