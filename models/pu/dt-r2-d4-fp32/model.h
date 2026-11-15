


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r2_d4_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[0] < 0.360167f) {
              if (features[19] < 0.142439f) {
                  if (features[22] < 0.774566f) {
                      if (features[12] < -0.737534f) {
                          return 0;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[10] < -0.705503f) {
                          return 1;
                      } else {
                          return 0;
                      }
                  }
              } else {
                  if (features[1] < 0.120115f) {
                      if (features[16] < 1.000993f) {
                          return 2;
                      } else {
                          return 0;
                      }
                  } else {
                      if (features[6] < -0.641707f) {
                          return 2;
                      } else {
                          return 1;
                      }
                  }
              }
          } else {
              if (features[12] < 1.505715f) {
                  if (features[10] < 0.634197f) {
                      if (features[11] < -0.508017f) {
                          return 2;
                      } else {
                          return 1;
                      }
                  } else {
                      if (features[10] < 0.691124f) {
                          return 1;
                      } else {
                          return 1;
                      }
                  }
              } else {
                  if (features[19] < 1.848935f) {
                      if (features[5] < 1.593431f) {
                          return 2;
                      } else {
                          return 1;
                      }
                  } else {
                      if (features[1] < 2.484490f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  }
              }
          }
        }
        

int32_t dt_r2_d4_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[3] = {0,};
        int32_t _class = -1;

        _class = dt_r2_d4_fp32_tree_0(features, features_length); votes[_class] += 1;
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<3; i++) {

            if (votes[i] > most_voted_votes) {
                most_voted_class = i;
                most_voted_votes = votes[i];
            }
        }
        return most_voted_class;
    }
    

int dt_r2_d4_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r2_d4_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    