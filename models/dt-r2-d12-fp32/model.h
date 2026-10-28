


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r2_d12_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[1] < 0.480359f) {
              if (features[14] < 0.916753f) {
                  if (features[10] < 0.775207f) {
                      if (features[20] < -0.437209f) {
                          if (features[23] < -0.969306f) {
                              if (features[17] < -0.795780f) {
                                  if (features[11] < -0.961203f) {
                                      return 8;
                                  } else {
                                      return 0;
                                  }
                              } else {
                                  return 2;
                              }
                          } else {
                              if (features[13] < 0.885954f) {
                                  if (features[11] < -0.110219f) {
                                      if (features[13] < -1.432014f) {
                                          return 2;
                                      } else {
                                          if (features[22] < -1.055818f) {
                                              if (features[19] < -0.196508f) {
                                                  return 8;
                                              } else {
                                                  return 2;
                                              }
                                          } else {
                                              return 8;
                                          }
                                      }
                                  } else {
                                      return 5;
                                  }
                              } else {
                                  return 4;
                              }
                          }
                      } else {
                          if (features[16] < -0.677792f) {
                              return 6;
                          } else {
                              if (features[23] < 0.668326f) {
                                  return 4;
                              } else {
                                  if (features[1] < -0.548666f) {
                                      return 6;
                                  } else {
                                      return 8;
                                  }
                              }
                          }
                      }
                  } else {
                      if (features[0] < 0.479456f) {
                          return 5;
                      } else {
                          return 0;
                      }
                  }
              } else {
                  return 1;
              }
          } else {
              if (features[11] < 0.845135f) {
                  return 3;
              } else {
                  if (features[8] < 0.843084f) {
                      return 7;
                  } else {
                      return 9;
                  }
              }
          }
        }
        

int32_t dt_r2_d12_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[10] = {0,};
        int32_t _class = -1;

        _class = dt_r2_d12_fp32_tree_0(features, features_length); votes[_class] += 1;
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<10; i++) {

            if (votes[i] > most_voted_votes) {
                most_voted_class = i;
                most_voted_votes = votes[i];
            }
        }
        return most_voted_class;
    }
    

int dt_r2_d12_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r2_d12_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    